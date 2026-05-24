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

Phase 0 ships two adapters behind the same Protocol:
  - `DbDerivedAdapter` (production) — pulls owned accounts and counterparty
    user_ids from Postgres on each call, with a short in-process TTL cache.
  - `SeedDerivedAdapter` (test/dev only) — builds bundles from the seed
    module so policy tests can run without a live database.
Phase 1 adds Okta/Entra/Auth0/OPA adapters with the same shape.

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

import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
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
    """The seam every entitlement source must implement."""

    def get_bundle(self, user_id: str) -> EntitlementBundle: ...


class UnknownUserError(KeyError):
    """Raised when get_bundle() is called with an id the adapter does not know."""


_COUNTERPARTY_FIELDS: frozenset[ClaimType] = frozenset({
    "transaction",       # name + amount + date + direction + memo (verified via source-span)
    "account_ownership", # naming a co-owner of an account the asking user owns
    "aggregate",         # totals/counts restricted to asking-user↔counterparty interaction
})


# ----- DbDerivedAdapter: production adapter ------------------------------


_DEFAULT_TTL_SECONDS = 30.0


class DbDerivedAdapter:
    """Production adapter: builds each bundle from a live Postgres lookup.

    Two SQL queries per cache miss — one for owned accounts, one for the
    counterparty user_ids the asking user has a transactional link to. A
    short-TTL in-process cache absorbs the burst of repeated lookups inside
    a single chat session without pinning a bundle long enough to outlive a
    real entitlement change. `invalidate()` is exposed for places that know
    they just mutated the underlying data (admin writes, test fixtures).

    The Phase-1 Okta/Entra/OPA adapters will implement the same Protocol —
    only `_fetch()` changes shape.
    """

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, EntitlementBundle]] = {}
        self._lock = Lock()

    def get_bundle(self, user_id: str) -> EntitlementBundle:
        cached = self._cached(user_id)
        if cached is not None:
            return cached
        bundle = self._fetch(user_id)
        self._store(user_id, bundle)
        return bundle

    def invalidate(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._cache.clear()
            else:
                self._cache.pop(user_id, None)

    def _cached(self, user_id: str) -> EntitlementBundle | None:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(user_id)
            if entry is None:
                return None
            expires, bundle = entry
            if now > expires:
                del self._cache[user_id]
                return None
            return bundle

    def _store(self, user_id: str, bundle: EntitlementBundle) -> None:
        with self._lock:
            self._cache[user_id] = (time.monotonic() + self._ttl, bundle)

    def _fetch(self, user_id: str) -> EntitlementBundle:
        from argo.db.queries import (
            get_account_ids_for_user,
            get_counterparty_user_ids_for_user,
            get_user,
        )

        if get_user(user_id) is None:
            raise UnknownUserError(user_id)

        accts = get_account_ids_for_user(user_id)
        cps = get_counterparty_user_ids_for_user(user_id)
        return EntitlementBundle(
            user_id=user_id,
            owned_subjects=frozenset({user_id, *accts}),
            counterparty_visible=frozenset(cps),
            counterparty_fields=_COUNTERPARTY_FIELDS,
        )


# ----- SeedDerivedAdapter: in-memory adapter for tests / dev REPL --------


class SeedDerivedAdapter:
    """In-memory adapter that builds bundles from `argo.db.seed` at load time.

    This adapter exists so the policy-engine test suite (`check_claim`) can
    run without a live Postgres. It is **not** used in production — the
    pipeline wires `DbDerivedAdapter`. If you import this class from
    anywhere outside `tests/` or an interactive REPL, you are almost
    certainly making a mistake.
    """

    def __init__(self) -> None:
        from argo.db.seed import USERS, accounts_for, counterparties_for

        self._bundles: dict[str, EntitlementBundle] = {
            u["id"]: EntitlementBundle(
                user_id=u["id"],
                owned_subjects=frozenset({u["id"]} | accounts_for(u["id"])),
                counterparty_visible=frozenset(counterparties_for(u["id"])),
                counterparty_fields=_COUNTERPARTY_FIELDS,
            )
            for u in USERS
        }

    def get_bundle(self, user_id: str) -> EntitlementBundle:
        try:
            return self._bundles[user_id]
        except KeyError as e:
            raise UnknownUserError(user_id) from e


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
