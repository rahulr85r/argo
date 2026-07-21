"""The reference instant that time-windowed policy rules evaluate against.

`recent_payment` asks "did these two parties transact within the last N days?"
That question needs a *now*, and until this module existed there were two
different answers: `argo/db/queries.py` used SQL `NOW()` while the seed
evaluator in `argo/db/seed.py` pinned itself to the latest seeded transaction.

Production and tests therefore disagreed, and — worse — the bundled demo
dataset silently decayed. Its transactions are fixed dates in Feb–May 2026, so
every day of real time moved more of them outside the 90-day window until the
counterparty graph collapsed to joint co-owners alone. Nothing failed loudly;
the demo just quietly stopped demonstrating.

Both call sites now read `reference_now()`, so there is exactly one clock.

`REFERENCE_TIME` selects it:

    unset / ""              wall clock (UTC). The production default — a real
                            bank's transactions are recent because they are
                            real, so "now" is the correct reference.
    "seed"                  one day past the newest transaction in the bundled
                            seed. Keeps the demo and the test suite stable
                            forever, and follows the data if the seed changes.
    ISO-8601 timestamp      an explicit pin, e.g. "2026-05-24T00:00:00Z".
                            Useful for reproducing a past entitlement decision
                            when auditing an old response.

Pinning the clock is a demo/test affordance, never a production posture: a
pinned reference freezes the counterparty graph, so stale relationships stop
ageing out. `DEPLOYMENT.md` says so next to the variable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from argo.config import settings

SEED_MODE = "seed"


class InvalidReferenceTimeError(ValueError):
    """`REFERENCE_TIME` was set to something that is neither 'seed' nor ISO-8601."""


def reference_now() -> datetime:
    """The instant time-windowed policy rules are evaluated against.

    Always timezone-aware UTC, so callers can subtract a timedelta and compare
    against `timestamptz` columns without surprises.
    """
    raw = (settings.reference_time or "").strip()

    if not raw:
        return datetime.now(UTC)

    if raw.lower() == SEED_MODE:
        # Imported lazily: argo.db.seed pulls in argo.db, and this module is
        # imported by argo.db.queries. Deferring keeps that cycle off the
        # import path — by the time anyone asks for a reference, seed is loaded.
        from argo.db.seed import SEED_REFERENCE_TIME

        return SEED_REFERENCE_TIME

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidReferenceTimeError(
            f"REFERENCE_TIME={raw!r} is not valid. Use '' (wall clock), "
            f"'seed' (pin to the bundled dataset), or an ISO-8601 timestamp "
            f"such as '2026-05-24T00:00:00Z'."
        ) from e

    # A naive pin is ambiguous; assume UTC rather than the host's local zone,
    # which would make the same config behave differently per deployment.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["reference_now", "InvalidReferenceTimeError", "SEED_MODE"]
