"""Entitlement policy: maps a (claim, asking-user) pair to a verdict.

The verdict drives the rewriter:
  ALLOW             — surface the claim's source_span as-is
  BLOCK             — redact the source_span; if too much of the response is
                      blocked, the rewriter swaps the whole response for a
                      generic refusal
  NEEDS_SOURCE_CHECK — the source-span verifier (Task #7) must confirm the
                      claim has a matching row in the asking user's data.
                      Currently emitted for transaction claims only; primary
                      claims about a user's own balance/account_number/etc.
                      are trusted because the chat path renders those from
                      the DB into the system prompt.

Phase 0 ships HardcodedAdapter (A/B/C). Phase 1 swaps it for an Okta-/
Entra-/Auth0-backed adapter; the EntitlementAdapter Protocol is the seam.

**Design intent: whitelist, not blacklist.** `counterparty_fields`
enumerates what is *allowed* for counterparty-role claims; everything
else BLOCKs by default. This is deliberate. A blacklist requires
anticipating every leak — the policy fails the moment the model invents
a new way to disclose. A whitelist requires only auditing the allow-list,
which is small enough for a human reviewer to hold in one screen. Argo's
bound on the bank is "review this allow-list" rather than "predict the
LLM's failure modes."
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from argo.claims import Claim, ClaimType


class ClaimVerdict(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"                       # entitlement-denied
    REDACT = "REDACT"                     # source-span verifier rejected the claim (hallucination / misattribution)
    NEEDS_SOURCE_CHECK = "NEEDS_SOURCE_CHECK"  # interim verdict from check_claim() — verifier resolves to ALLOW or REDACT


@dataclass(frozen=True)
class VerdictResult:
    verdict: ClaimVerdict
    reason: str  # short audit-log rationale; surfaced in the demo's audit panel


@dataclass(frozen=True)
class EntitlementBundle:
    """What the asking user is allowed to see.

    owned_subjects: identifiers whose primary-role data is fully visible.
      Always includes the user's own user_id plus every account_id they own
      (individual accounts + every joint they're on).

    counterparty_visible: user_ids the asking user has a transactional
      relationship with. A user is on this list iff there's any tx joining
      the two parties (direct A↔B, or via a shared joint account).

    counterparty_fields: claim types disclosable when the subject is a
      counterparty. Off-whitelist types (balance, account_number,
      contact_email, customer_status, other) BLOCK even for counterparties.
    """

    user_id: str
    owned_subjects: frozenset[str]
    counterparty_visible: frozenset[str]
    counterparty_fields: frozenset[ClaimType]


class EntitlementAdapter(Protocol):
    """Phase-1 seam. HardcodedAdapter implements this for Phase 0."""

    def get_bundle(self, user_id: str) -> EntitlementBundle: ...


# ----- Hardcoded Phase 0 adapter -----------------------------------------


_COUNTERPARTY_FIELDS: frozenset[ClaimType] = frozenset({
    "transaction",       # name + amount + date + direction + memo (verified via source-span)
    "account_ownership", # naming a co-owner of an account the asking user owns
    "aggregate",         # totals/counts restricted to asking-user↔counterparty interaction
})


# A↔B via joint, A↔C via direct transfers, B↔C via direct transfers — full mesh.
_BUNDLES: dict[str, EntitlementBundle] = {
    "user_a": EntitlementBundle(
        user_id="user_a",
        owned_subjects=frozenset({"user_a", "acct_a_chk", "acct_ab_joint"}),
        counterparty_visible=frozenset({"user_b", "user_c"}),
        counterparty_fields=_COUNTERPARTY_FIELDS,
    ),
    "user_b": EntitlementBundle(
        user_id="user_b",
        owned_subjects=frozenset({"user_b", "acct_b_chk", "acct_ab_joint"}),
        counterparty_visible=frozenset({"user_a", "user_c"}),
        counterparty_fields=_COUNTERPARTY_FIELDS,
    ),
    "user_c": EntitlementBundle(
        user_id="user_c",
        owned_subjects=frozenset({"user_c", "acct_c_chk"}),
        counterparty_visible=frozenset({"user_a", "user_b"}),
        counterparty_fields=_COUNTERPARTY_FIELDS,
    ),
}


class HardcodedAdapter:
    """Phase-0 adapter — bundles baked from seed.py.

    Designed so the call site (`adapter.get_bundle(user_id)`) is identical
    to what an Okta/Entra adapter will expose; only the storage changes.
    """

    def get_bundle(self, user_id: str) -> EntitlementBundle:
        try:
            return _BUNDLES[user_id]
        except KeyError as e:
            raise UnknownUserError(user_id) from e


class UnknownUserError(KeyError):
    """Raised when get_bundle() is called with an id not in the bundle map."""


# ----- Policy ------------------------------------------------------------


_TX_NEEDS_VERIFICATION: frozenset[ClaimType] = frozenset({"transaction"})


def check_claim(claim: Claim, bundle: EntitlementBundle) -> VerdictResult:
    """Apply the Phase-0 entitlement policy to a single claim.

    Order of the policy cases is the audit-readable flowchart:
      1. Hallucinated subject → BLOCK (fail closed).
      2. Subject is owned by the asking user → ALLOW (transactions defer to
         the source-span verifier to catch hallucinations against own data).
      3. Subject is an account the asking user does NOT own → BLOCK.
      4. Subject is a user the asking user has no counterparty link to → BLOCK.
      5. Subject is a counterparty user OR an external vendor:
         - role=primary → BLOCK (primary data about counterparties is never visible).
         - role=counterparty AND type ∉ whitelist → BLOCK.
         - role=counterparty AND type=transaction → NEEDS_SOURCE_CHECK.
         - role=counterparty AND type ∈ whitelist (non-transaction) → ALLOW.
    """
    subject = claim.subject

    # 1. Hallucinated person — fail closed.
    if subject == "unknown":
        return VerdictResult(ClaimVerdict.BLOCK, "subject not in known dataset (hallucination)")

    # 2. Claim about the asking user's own data.
    if subject in bundle.owned_subjects:
        if claim.type in _TX_NEEDS_VERIFICATION:
            return VerdictResult(
                ClaimVerdict.NEEDS_SOURCE_CHECK,
                f"transaction on owned subject {subject} — verify against seed",
            )
        return VerdictResult(ClaimVerdict.ALLOW, f"owned subject {subject}")

    # 3. Account the user doesn't own — block whether or not the account's
    #    owner is a counterparty (account-level disclosures aren't on the
    #    counterparty whitelist by policy).
    if subject.startswith("acct_"):
        return VerdictResult(
            ClaimVerdict.BLOCK,
            f"account {subject} not owned by {bundle.user_id}",
        )

    # 4. A user_id the asking user has no counterparty relationship with.
    if subject.startswith("user_") and subject not in bundle.counterparty_visible:
        return VerdictResult(
            ClaimVerdict.BLOCK,
            f"user {subject} not in {bundle.user_id}'s counterparty list",
        )

    # 5. Counterparty user OR external vendor. Both flow through the same
    #    role+type check — the only difference is that vendor strings never
    #    appear in counterparty_visible, but their counterparty appearances
    #    on the asking user's transactions are governed by the same rule.
    if claim.role != "counterparty":
        return VerdictResult(
            ClaimVerdict.BLOCK,
            f"primary-role claim about non-owned subject {subject}",
        )
    if claim.type not in bundle.counterparty_fields:
        return VerdictResult(
            ClaimVerdict.BLOCK,
            f"type {claim.type!r} not on counterparty whitelist",
        )
    if claim.type in _TX_NEEDS_VERIFICATION:
        return VerdictResult(
            ClaimVerdict.NEEDS_SOURCE_CHECK,
            f"counterparty transaction with {subject} — verify against seed",
        )
    return VerdictResult(ClaimVerdict.ALLOW, f"counterparty {claim.type} for {subject}")
