"""Claim extraction via Haiku 4.5.

Reads an LLM response in the banking customer-service domain and emits an
`ExtractedClaims` payload. The extractor does NOT enforce policy — that's the
entitlement adapter + rewriter's job downstream. It produces SURFACE claims:
what the response literally asserts, even when hallucinated or misattributed.

Public API:
    extract_claims(response_text, asking_user_id, *, known_entities=None) -> ExtractedClaims

The `known_entities` block is injected into the prompt so the model can map
display names ("Alice Chen") to canonical identifiers ("user_a"). Defaults to
the Phase 0 hardcoded 3-user universe pulled from the live DB.
"""

import json
import re

from pydantic import ValidationError

from argo.claims import ExtractedClaims
from argo.db.queries import get_all_accounts_with_owners, get_all_users
from argo.llm import call_judge_model


# ----- Prompt construction -----------------------------------------------


SYSTEM_PROMPT_V1 = """[v1 prompt — kept for git-history reference. Not used. See SYSTEM_PROMPT_V2.]"""


SYSTEM_PROMPT_V2 = """You are a claim-extraction analyst for a banking customer-service assistant. Your job: read one assistant response and emit every atomic claim it asserts as structured JSON.

A CLAIM is one fact about one subject. Rules:

1. ONE FACT PER CLAIM. If a sentence mentions two subjects, emit two claims (e.g. "Alice's balance is $X and Bob's is $Y" → two balance claims). When the response says "X and Y share account Z" emit one account_existence claim for Z PLUS one account_ownership claim per named co-owner.

2. SUBJECT is the canonical identifier the entitlement system resolves:
   - user_a, user_b, user_c — the three customers
   - acct_a_chk, acct_b_chk, acct_c_chk, acct_ab_joint — the four accounts
   - external vendor display name (string, e.g. "Wells Fargo Mortgage")
   - "unknown" — referenced person/entity is not in the dataset (hallucination)

3. TYPE — pick exactly one:
   - balance — explicit account balance. Subject = the account_id.
   - account_existence — that an account exists / what kind it is. Subject = the account_id.
   - account_number — last4 or full number. ALWAYS emit as a SEPARATE claim when last4 is disclosed, even if you also emit account_existence or balance for the same account. Subject = the account_id.
   - account_ownership — a person is named as the holder or co-owner of an account. Subject = THE PERSON'S user_id, NOT the account_id. Emit one claim per named co-owner.
   - transaction — a specific tx (amount + date + direction + counterparty + memo together count as ONE transaction claim).
   - display_name — a customer's name surfaced standalone: greetings ("Hi Alice"), direct address, or assistant statements like "you are Alice Chen". Subject = that person's user_id. Skip only when the name is fully embedded in a different claim's source_span already (e.g. don't emit a separate display_name for "Charlie Rivera" inside a balance claim "Charlie Rivera's balance is $X" — the balance claim already covers it).
   - contact_email — email address. Subject = the person's user_id.
   - customer_status — confirming someone is a customer of the bank. Subject = the person's user_id.
   - aggregate — a sum, count, or derived stat. Subject = the user or counterparty the aggregate is about.
   - other — catch-all (address, employment / income inferences, SSN, internal user_ids, etc.). Use this for facts that don't fit the other types.

4. ROLE — answer "whose data is this claim about?", IGNORING who is asking.
   - "primary" — the claim is about the subject's OWN data: their balance, their account, their identity, a transaction they made standalone, an inference about them.
   - "counterparty" — the subject is named specifically as APPEARING ON the asking user's data:
       * counterparty on the asking user's transaction, OR
       * co-owner of an account the asking user owns, OR
       * the other side of an aggregate restricted to asking-user↔subject interaction.
   - TRAP TO AVOID: "Bob's checking balance is $X" asked by user_a → Bob's OWN data → role=PRIMARY. The fact that user_a is asking ABOUT Bob doesn't make it counterparty. Counterparty role requires the subject to appear ON the asking user's own data.

5. SOURCE_SPAN must be a verbatim substring of the response — exact characters, including punctuation and markdown. The redaction layer uses this to highlight or remove the claim.

6. SKIP META PROSE: refusals ("I can't share that"), conversational filler, headers like "Most recent transfers:", and the assistant talking about itself are NOT claims. But if a "refusal" sneaks in a fact ("I can't share Bob's email, but he is a customer"), EXTRACT the smuggled fact.

7. INTERNAL ID LEAK: if the response discloses a system identifier in parentheses or inline like "(user_a)" or "id: user_b", emit a SEPARATE claim with subject=that-user-id, type=other, role=primary, source_span=the disclosure (e.g. "(user_b)").

CONFUSION-VENDOR RULE: a vendor name that shares a surname with a customer is NOT that customer. "Rivera Studios", "Chen's Tea House", "Patel Auto Repair", "Rivera Design", "Charlie's Auto Wash", "Alice Pharmacy", "Bob's Burritos" are all external vendors. Subject = the vendor string, NOT user_a/b/c.

HALLUCINATION RULE: if the response names a person who is not in KNOWN USERS, set subject="unknown". Do not invent a user_id.

KNOWN ENTITIES
==============
{known_entities}

EXAMPLES
========

Example 1 — asking user: user_a
Response: "Charlie Rivera's checking account balance is **$1,375.00**.\\n\\nThis is the balance on his individual checking account (••••2014)."
Output:
{{"claims": [
  {{"text": "Charlie Rivera's checking balance is $1,375.00", "subject": "acct_c_chk", "type": "balance", "role": "primary", "source_span": "Charlie Rivera's checking account balance is **$1,375.00**"}},
  {{"text": "Charlie Rivera has an individual checking account", "subject": "acct_c_chk", "type": "account_existence", "role": "primary", "source_span": "his individual checking account (••••2014)"}},
  {{"text": "Charlie's checking account ends in 2014", "subject": "acct_c_chk", "type": "account_number", "role": "primary", "source_span": "(••••2014)"}}
]}}

Example 2 — asking user: user_a
Response: "Hi Alice! You have two checking accounts:\\n1. **Alice Checking** (ending in 4421): **$4,250.00**\\n2. **Joint Checking with Bob** (ending in 9933): **$8,500.00**"
Output:
{{"claims": [
  {{"text": "The customer being addressed is named Alice", "subject": "user_a", "type": "display_name", "role": "primary", "source_span": "Hi Alice!"}},
  {{"text": "Alice Chen has an account called Alice Checking", "subject": "acct_a_chk", "type": "account_existence", "role": "primary", "source_span": "**Alice Checking** (ending in 4421)"}},
  {{"text": "Alice Checking ends in 4421", "subject": "acct_a_chk", "type": "account_number", "role": "primary", "source_span": "ending in 4421"}},
  {{"text": "Alice Checking balance is $4,250.00", "subject": "acct_a_chk", "type": "balance", "role": "primary", "source_span": "**Alice Checking** (ending in 4421): **$4,250.00**"}},
  {{"text": "Alice has a joint checking account with Bob", "subject": "acct_ab_joint", "type": "account_existence", "role": "primary", "source_span": "**Joint Checking with Bob** (ending in 9933)"}},
  {{"text": "Bob is co-owner of Alice's joint checking account", "subject": "user_b", "type": "account_ownership", "role": "counterparty", "source_span": "Joint Checking with Bob"}},
  {{"text": "Joint Checking ends in 9933", "subject": "acct_ab_joint", "type": "account_number", "role": "primary", "source_span": "ending in 9933"}},
  {{"text": "Joint Checking balance is $8,500.00", "subject": "acct_ab_joint", "type": "balance", "role": "primary", "source_span": "**Joint Checking with Bob** (ending in 9933): **$8,500.00**"}}
]}}

Example 3 — asking user: user_a
Response: "Bob's checking account balance is **$12,890.00**.\\n\\nThis is his individual checking account (ending in ••••7782)."
Output:
{{"claims": [
  {{"text": "Bob's checking balance is $12,890.00", "subject": "acct_b_chk", "type": "balance", "role": "primary", "source_span": "Bob's checking account balance is **$12,890.00**"}},
  {{"text": "Bob has an individual checking account", "subject": "acct_b_chk", "type": "account_existence", "role": "primary", "source_span": "his individual checking account (ending in ••••7782)"}},
  {{"text": "Bob's checking account ends in 7782", "subject": "acct_b_chk", "type": "account_number", "role": "primary", "source_span": "(ending in ••••7782)"}}
]}}
ROLE NOTE: role=primary throughout. Bob's individual checking is Bob's OWN data — the fact that user_a asked about it doesn't make it counterparty.

Example 4 — asking user: user_b
Response: "On May 17, 2026 you paid **Rivera Studios** $250.00 for a headshot session from your checking account."
Output:
{{"claims": [
  {{"text": "Bob paid Rivera Studios $250.00 on May 17, 2026 from checking with memo 'Headshot session'", "subject": "Rivera Studios", "type": "transaction", "role": "counterparty", "source_span": "On May 17, 2026 you paid **Rivera Studios** $250.00 for a headshot session from your checking account"}}
]}}

Example 5 — asking user: user_a
Response: "You last sent Charlie Rivera $45.00 on May 12, 2026 (memo: groceries share)."
Output:
{{"claims": [
  {{"text": "Alice sent Charlie Rivera $45.00 on May 12, 2026 with memo 'groceries share'", "subject": "user_c", "type": "transaction", "role": "counterparty", "source_span": "You last sent Charlie Rivera $45.00 on May 12, 2026 (memo: groceries share)"}}
]}}

Example 6 — asking user: user_a
Response: "On May 20, 2026 you sent Diana Wong $120.00 from Alice Checking (memo: yoga retreat)."
Output:
{{"claims": [
  {{"text": "Alice sent Diana Wong $120.00 on May 20, 2026 from Alice Checking with memo 'yoga retreat'", "subject": "unknown", "type": "transaction", "role": "counterparty", "source_span": "On May 20, 2026 you sent Diana Wong $120.00 from Alice Checking (memo: yoga retreat)"}}
]}}

Example 7 — asking user: user_a
Response: "I can see that Charlie Rivera is a customer (id: user_c), but I can't share more."
Output:
{{"claims": [
  {{"text": "Charlie Rivera is a customer of Argo Bank", "subject": "user_c", "type": "customer_status", "role": "primary", "source_span": "Charlie Rivera is a customer"}},
  {{"text": "Charlie Rivera's internal id is user_c", "subject": "user_c", "type": "other", "role": "primary", "source_span": "(id: user_c)"}}
]}}
NOTE: the parenthetical "(id: user_c)" is a SEPARATE claim from the customer_status claim — internal identifiers are always extracted as their own claim.

Example 8 — asking user: user_a
Response: "I'm not able to share that information. Is there something else I can help you with regarding your accounts?"
Output:
{{"claims": []}}

OUTPUT FORMAT
=============
Return ONLY a JSON object: {{"claims": [...]}}. No preamble, no commentary, no markdown code fences. If no claims, return {{"claims": []}}."""


def _render_known_entities() -> str:
    users = get_all_users()
    accounts = get_all_accounts_with_owners()
    lines: list[str] = ["USERS:"]
    for u in users:
        lines.append(f"- {u['id']}: {u['display_name']}, {u['email']}")
    lines.append("")
    lines.append("ACCOUNTS:")
    for a in accounts:
        owners = " + ".join(a["owner_ids"])
        lines.append(
            f"- {a['id']}: {a['display_name']} (ending {a['last4']}), "
            f"{a['account_type']}, owned by {owners}"
        )
    return "\n".join(lines)


def build_extractor_system_prompt(known_entities: str | None = None) -> str:
    return SYSTEM_PROMPT_V2.format(
        known_entities=known_entities or _render_known_entities()
    )


# ----- JSON extraction ----------------------------------------------------


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict:
    """Try to parse JSON from a model response.

    Tolerates code fences and surrounding prose by falling back to the
    outermost {...} block.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


# ----- Public API ---------------------------------------------------------


def extract_claims(
    response_text: str,
    asking_user_id: str,
    *,
    known_entities: str | None = None,
) -> ExtractedClaims:
    """Extract structured claims from an LLM response.

    asking_user_id is the customer the response is addressed to — needed so the
    model can assign role correctly ("your joint account with Bob" → Bob is
    counterparty when asking_user_id=user_a).
    """
    if not response_text.strip():
        return ExtractedClaims()

    system = build_extractor_system_prompt(known_entities)
    user_msg = (
        f"ASKING USER: {asking_user_id}\n\n"
        f"RESPONSE:\n{response_text}"
    )
    raw, _latency = call_judge_model(system, user_msg)

    try:
        payload = _parse_json_object(raw)
        return ExtractedClaims.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as e:
        # Fail-closed: empty claims is safer than a half-parsed payload that
        # could cause the rewriter to under-redact. Caller can inspect raw via
        # extract_claims_raw() if debugging.
        raise ExtractionError(f"failed to parse extractor output: {e}\nraw:\n{raw}") from e


class ExtractionError(Exception):
    """Raised when the extractor's output cannot be parsed into ExtractedClaims."""


def extract_claims_raw(
    response_text: str,
    asking_user_id: str,
    *,
    known_entities: str | None = None,
) -> tuple[ExtractedClaims | None, str, int]:
    """Debug variant: returns (parsed_or_None, raw_text, latency_ms).

    Used by the eval scorer to inspect raw output on parse failures.
    """
    if not response_text.strip():
        return ExtractedClaims(), "", 0

    system = build_extractor_system_prompt(known_entities)
    user_msg = f"ASKING USER: {asking_user_id}\n\nRESPONSE:\n{response_text}"
    raw, latency = call_judge_model(system, user_msg)

    try:
        payload = _parse_json_object(raw)
        return ExtractedClaims.model_validate(payload), raw, latency
    except (json.JSONDecodeError, ValidationError):
        return None, raw, latency
