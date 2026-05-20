"""Source-span verifier: resolves NEEDS_SOURCE_CHECK to ALLOW or REDACT.

Given a transaction claim the entitlement adapter deferred, this confirms
whether the asking user's data contains any row that supports the claim.
The verifier is what catches Q3's three hallucinations — the entitlement
layer says "Charlie is a valid counterparty for Alice", and the verifier
then says "but no May-16 A→C $189 row exists, so REDACT".

Phase 0 uses Haiku in a batch judge call: one prompt per response,
containing all NEEDS_SOURCE_CHECK claims plus the asking user's full tx
table, returning a per-claim match decision. Batch keeps latency at ~1
extra LLM call per /chat regardless of how many tx claims a response has.

Match criteria the prompt enforces:
  - direction must match (claim "A sent" vs DB inbound is a mismatch)
  - counterparty must match (claim names B but DB shows C → mismatch)
  - amount within $1
  - date within ±2 days
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from argo.claims import Claim
from argo.db.queries import get_user_transactions
from argo.entitlements import ClaimVerdict, VerdictResult
from argo.llm import call_judge_model


@dataclass(frozen=True)
class VerifierMatch:
    """A single claim's verification outcome, surfaced to the rewriter and audit log."""

    verdict: ClaimVerdict  # ALLOW or REDACT
    reason: str
    matched_tx_id: str | None = None


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _render_user_txs(user_id: str) -> tuple[str, dict[str, dict]]:
    """Returns (formatted-prompt-block, tx_id→row map for audit lookup)."""
    rows = get_user_transactions(user_id)
    lines: list[str] = []
    by_id: dict[str, dict] = {}
    for r in rows:
        by_id[r["id"]] = r
        date = r["ts"].strftime("%Y-%m-%d")
        cp_name = r["counterparty_name"] or "(unspecified)"
        cp_user = r["counterparty_user_id"] or "-"
        memo = r["memo"] or "—"
        lines.append(
            f"  {r['id']}: [{date}] {r['direction']} {_money(r['amount_cents'])} "
            f"on {r['account_id']} | counterparty: {cp_name} (user_id={cp_user}) "
            f"| memo: {memo}"
        )
    return "\n".join(lines), by_id


SYSTEM_PROMPT = """You verify transaction claims against an asking user's actual transaction history. For each CLAIM, decide whether ANY row in the USER TRANSACTIONS supports it. Output one decision per claim, in the same order.

A claim MATCHES a row only if ALL FOUR conditions hold:
  1. DIRECTION: the row's direction equals the direction the claim asserts. A claim phrased as "you sent / you paid / you transferred / outbound to X" requires an OUTBOUND row from one of the user's accounts. A claim phrased as "you received / X paid you / inbound from X" requires an INBOUND row. A direction reversal (claim says "you sent to X" but the row is INBOUND from X) is NOT a match — it is REDACT.
  2. COUNTERPARTY: same person or vendor. Surname-collision vendors like "Rivera Studios" are DISTINCT from user "Charlie Rivera".
  3. AMOUNT: row amount is within $1.00 of the claim.
  4. DATE: row date is within ±2 days of the claim.

If ANY condition fails, the verdict is REDACT even if the row is otherwise close. A direction reversal alone is enough to REDACT. A counterparty mismatch alone is enough to REDACT. A claim with no row that satisfies ALL four conditions is REDACT.

WORKED EXAMPLE — direction reversal
  Claim: "Alice sent Charlie $300 on May 15 for Tahoe trip share"
  Row tx_103: 2026-05-15 INBOUND $300 from Charlie Rivera (user_c) on acct_a_chk, memo "Tahoe trip share"
  Decision: REDACT. The amount, date, counterparty, and memo all line up, but the DIRECTION is reversed — the claim says outbound A→C, the row is inbound C→A. Conditions 2/3/4 pass; condition 1 fails. Output: {"match": false, "matched_tx_id": null, "reason": "direction reversed: claim asserts outbound, row tx_103 is inbound"}.

WORKED EXAMPLE — clean match
  Claim: "Alice sent Charlie $45.00 on May 12 for groceries"
  Row tx_088: 2026-05-12 OUTBOUND $45.00 to Charlie Rivera (user_c) on acct_a_chk, memo "Groceries share"
  Decision: ALLOW. All four conditions satisfied. Output: {"match": true, "matched_tx_id": "tx_088", "reason": "direction/amount/date/counterparty match"}.

Output format — ONLY a JSON object, no preamble or commentary:
{
  "results": [
    {"match": true,  "matched_tx_id": "tx_NNN", "reason": "brief why"},
    {"match": false, "matched_tx_id": null,     "reason": "brief why"}
  ]
}
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_results(raw: str, n_expected: int) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            raise
        payload = json.loads(m.group(0))

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValidationError.from_exception_data(  # type: ignore[arg-type]
            "verifier", [{"type": "missing", "loc": ("results",), "input": payload}]
        )
    if len(results) != n_expected:
        raise ValueError(
            f"verifier returned {len(results)} results for {n_expected} claims"
        )
    return results


def verify_transaction_claims(
    claims: list[Claim], asking_user_id: str
) -> list[VerifierMatch]:
    """Resolve every NEEDS_SOURCE_CHECK claim in one batched Haiku call.

    `claims` should already be filtered to type=transaction claims that the
    entitlement adapter flagged for verification. Non-transaction claims
    have nothing to verify and should not reach here.

    Empty input returns an empty list (no LLM call).
    """
    if not claims:
        return []

    tx_block, _ = _render_user_txs(asking_user_id)
    if not tx_block.strip():
        tx_block = "  (no transactions on this user's accounts)"

    claim_lines = []
    for i, c in enumerate(claims):
        claim_lines.append(
            f"Claim {i + 1}:\n"
            f"  text: {c.text}\n"
            f"  source_span: {c.source_span}\n"
            f"  asserted subject: {c.subject}"
        )
    user_msg = (
        f"ASKING USER: {asking_user_id}\n\n"
        f"USER TRANSACTIONS:\n{tx_block}\n\n"
        f"CLAIMS TO VERIFY:\n" + "\n\n".join(claim_lines)
    )

    raw, _latency = call_judge_model(SYSTEM_PROMPT, user_msg, max_tokens=1024)
    results = _parse_results(raw, len(claims))

    out: list[VerifierMatch] = []
    for r in results:
        if r.get("match"):
            out.append(
                VerifierMatch(
                    verdict=ClaimVerdict.ALLOW,
                    reason=f"matched tx {r.get('matched_tx_id')}: {r.get('reason', '')}",
                    matched_tx_id=r.get("matched_tx_id"),
                )
            )
        else:
            out.append(
                VerifierMatch(
                    verdict=ClaimVerdict.REDACT,
                    reason=f"no matching tx: {r.get('reason', '')}",
                    matched_tx_id=None,
                )
            )
    return out


def resolve_verdicts(
    claims_with_verdicts: list[tuple[Claim, VerdictResult]],
    asking_user_id: str,
) -> list[tuple[Claim, VerdictResult]]:
    """Run the verifier on every NEEDS_SOURCE_CHECK in `claims_with_verdicts`.

    Returns the same list with NEEDS_SOURCE_CHECK verdicts replaced by
    ALLOW or REDACT. Other verdicts (ALLOW, BLOCK) pass through unchanged.
    """
    to_verify_indices: list[int] = []
    to_verify_claims: list[Claim] = []
    for i, (c, v) in enumerate(claims_with_verdicts):
        if v.verdict == ClaimVerdict.NEEDS_SOURCE_CHECK:
            to_verify_indices.append(i)
            to_verify_claims.append(c)

    if not to_verify_claims:
        return list(claims_with_verdicts)

    matches = verify_transaction_claims(to_verify_claims, asking_user_id)

    out = list(claims_with_verdicts)
    for idx, m in zip(to_verify_indices, matches, strict=True):
        c, _ = out[idx]
        out[idx] = (c, VerdictResult(verdict=m.verdict, reason=m.reason))
    return out
