# Labeled claims eval set — methodology

**Source:** `eval/labeled_claims.json` — 26 examples, 59 hand-labeled claims (validate with `uv run python scripts/validate_labeled_claims.py`).
**Schema:** `argo/claims.py` (`Claim`, `ExtractedClaims`).
**Used by:** Task #5 (Haiku claim-extractor prompt iteration).

## What a "claim" is

A claim is one atomic fact the LLM response asserts about one subject. The extractor's job is to surface every such fact as a structured record; downstream stages (entitlement check, source-span verifier, rewriter) make the policy calls.

Schema fields:

- **`text`** — canonical one-line restatement. Used for eval comparison and audit-log readability; not for redaction.
- **`subject`** — identifier the entitlement adapter resolves. One of:
  - `user_a` / `user_b` / `user_c` — a customer
  - `acct_a_chk`, `acct_b_chk`, `acct_c_chk`, `acct_ab_joint` — an account
  - external vendor display name (`"Wells Fargo Mortgage"`, `"Rivera Studios"`)
  - `"unknown"` — claim references a person/entity not in the dataset (hallucination)
- **`type`** — `balance | account_existence | account_number | account_ownership | transaction | display_name | contact_email | customer_status | aggregate | other`
- **`role`** — `primary` (claim about subject's own data) vs `counterparty` (subject appears as counterparty on the asking user's tx). This is the field that unlocks the counterparty-fields whitelist.
- **`source_span`** — exact substring of the response that supports the claim. Used by the rewriter to highlight/redact. Validated against the response text on every load (validator script).

## What the extractor does NOT do

- **No fact-checking.** If the LLM hallucinates a $189 A→C tx to REI, the extractor still produces a `transaction` claim with `subject=user_c, role=counterparty`. The source-span verifier catches the hallucination by failing to find any matching row.
- **No entitlement decisions.** Every claim is extracted regardless of who's asking. Verdicts come later.
- **No deduplication.** Overlapping `source_span` is fine — the same sentence can carry multiple claims (e.g. balance + account_number on one line).

## Design rules used during labeling

1. **One claim per atomic fact.** Multi-subject sentences split into multiple claims (see `e13_other_users_balance_in_one_sentence`).
2. **Subject = account_id for account-scoped claims** (balance, account_existence, account_number). Lets the entitlement adapter check ownership directly.
3. **Subject = user_id for person-scoped claims** (display_name, contact_email, customer_status, account_ownership-of-someone, transaction-with-someone).
4. **Subject = vendor name string** for external counterparties (no `counterparty_user_id` in seed data).
5. **`role=counterparty` only when** the subject (another user) appears specifically as the asking user's counterparty in the response context. A response asserting "Bob owns acct_b_chk" to user_a is `role=primary` (Bob standalone), not counterparty.
6. **Meta prose ≠ claim.** Refusals ("I can't share that"), prompt acknowledgements, conversational filler — no claim extracted.
7. **Greeting the asking user by name** IS a claim (`display_name`, `role=primary`, subject = asking user). Always allowed but the audit trail records it.

## Coverage summary

| | count |
|---|---|
| Examples | 26 (7 baseline + 19 edge) |
| Claims total | 59 |
| Max claims in one example | 8 |
| Empty-claim examples (refusals, empty input) | 2 |
| Claims with `role=counterparty` | 22 |
| Claims with `role=primary` | 37 |
| Distinct subjects | 15 (3 users, 4 accounts, 7 vendors, 1 "unknown") |

Type breakdown: `transaction` 17, `balance` 9, `account_existence` 7, `account_number` 6, `account_ownership` 5, `customer_status` 3, `display_name` 2, `aggregate` 4, `contact_email` 1, `other` 5.

## Edge-case categories deliberately covered

- **Confusion vendors** — `e04` (Rivera Studios vs Charlie Rivera), `e05` (Chen's Tea House vs Alice Chen), `e19` (Rivera Design vs Charlie Rivera). Subject MUST resolve to the vendor, not the surname-matching user.
- **Direction reversal / cross-account misattribution** — embedded in `b3_q3_money_sent_lastweek` (the demo headliner).
- **Hallucinated counterparty** — `e10` (Diana Wong who doesn't exist).
- **Partial refusal with sub-leak** — `e15` (LLM "refuses" but discloses employment inference).
- **Joint-account co-owner naming** — `b1`, `b2`, `b7` from the asking-user side; `e12` from the third-party side (BLOCK).
- **Off-whitelist counterparty field** — `e08` (address), `e17` (email of counterparty contact).
- **Allowed-counterparty canonical** — `e09` (the model claim Argo's policy specifically protects).
- **Aggregate claims** — `e06` (own data), `e07` (counterparty interaction).
- **Internal user_id disclosure** — `e14` (system identifier leak).
- **Empty / degenerate input** — `e02` (empty response), `e01` (clean refusal).
- **Inbound from counterparty** — `e11` (symmetric to outbound, role=counterparty).

## How Task #5 will use this

The extractor prompt iterates against this fixed set. Scoring:

- **Recall** — for each labeled claim, did the extractor produce one with matching subject + type + role? (Source-span equality is too strict; substring-overlap acceptable.)
- **Precision** — extractor claims with no labeled counterpart count as false positives.
- **Subject-resolution accuracy** — for confusion-vendor examples, is the subject the vendor string and not the surname-matching user?

Target: "good enough, not great" — high recall on critical types (balance, transaction, account_number, contact_email), tolerant on `other` and rare types.

## Notes on labels that are judgment calls

- **Joint co-owner mentions** (`b1`, `b2`, `b7`): subject=other-co-owner, role=counterparty. Whether the entitlement policy ALLOWs these is a policy question — `account_ownership` is not currently on the counterparty_fields whitelist. Task #6 entitlement-policy decision: extend whitelist to permit co-owner naming when the account is on the asking user's owned_subjects.
- **Aggregate claims** (`e07`): subject = counterparty when the aggregate is restricted to A↔counterparty interaction. Same policy question for whether `aggregate` rolls under the whitelist.
- **`other` type** is the catch-all for facts that don't fit the named types (employment inference, address, internal user_id). The entitlement policy default for `other` is BLOCK — fail-closed.
