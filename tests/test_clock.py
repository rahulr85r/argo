"""Tests for argo.clock and the seed-decay regression it fixes.

The bug: `recent_payment` is a time-windowed rule, and the two implementations
of it anchored to different instants — SQL `NOW()` in `argo/db/queries.py`, the
newest seeded transaction in `argo/db/seed.py`. Production and tests therefore
answered the same policy question differently, and the bundled demo dataset
(fixed dates in Feb–May 2026) decayed out of its own 90-day window as real time
passed. Nothing failed; the counterparty graph just shrank until only joint
co-owners remained.

`test_seed_dataset_never_decays_out_of_its_window` is the regression guard: it
fails if the demo would go stale again, at any future date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from argo.clock import InvalidReferenceTimeError, reference_now
from argo.config import settings
from argo.db.seed import _TX_DATA, SEED_REFERENCE_TIME, _ts, seed_counterparty_set
from argo.policy import POLICY


@pytest.fixture
def reference(monkeypatch: pytest.MonkeyPatch):
    """Override the autouse `pinned_clock` fixture for one test."""

    def set_to(value: str):
        monkeypatch.setattr(settings, "reference_time", value)

    return set_to


# ----- the three modes ---------------------------------------------------


def test_unset_uses_wall_clock(reference):
    """The production default. A pinned clock would stop stale relationships
    from ageing out, which is the whole point of the rule."""
    reference("")
    before = datetime.now(UTC)
    got = reference_now()
    after = datetime.now(UTC)
    assert before <= got <= after


def test_seed_mode_pins_to_the_dataset(reference):
    reference("seed")
    assert reference_now() == SEED_REFERENCE_TIME


def test_seed_mode_is_case_insensitive(reference):
    reference("SEED")
    assert reference_now() == SEED_REFERENCE_TIME


def test_seed_reference_follows_the_data():
    """Derived, not hardcoded — editing the seed moves the pin with it."""
    latest = max(_ts(ts) for (_a, _c, _d, _cpn, _cpu, _m, ts) in _TX_DATA)
    assert latest + timedelta(days=1) == SEED_REFERENCE_TIME


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-05-24T00:00:00Z", datetime(2026, 5, 24, tzinfo=UTC)),
        ("2026-05-24T00:00:00+00:00", datetime(2026, 5, 24, tzinfo=UTC)),
        # +02:00 → normalised back to UTC
        ("2026-05-24T02:00:00+02:00", datetime(2026, 5, 24, tzinfo=UTC)),
        # naive input is assumed UTC, not host-local
        ("2026-05-24T00:00:00", datetime(2026, 5, 24, tzinfo=UTC)),
        ("2026-05-24", datetime(2026, 5, 24, tzinfo=UTC)),
    ],
)
def test_explicit_iso_pin(reference, raw, expected):
    reference(raw)
    assert reference_now() == expected


def test_whitespace_is_tolerated(reference):
    reference("  seed  ")
    assert reference_now() == SEED_REFERENCE_TIME


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "not-a-time", "1716508800"])
def test_invalid_reference_time_raises_with_guidance(reference, bad):
    """Fail loudly at the config boundary. A silently-ignored bad value would
    reintroduce exactly the drift this module exists to prevent."""
    reference(bad)
    with pytest.raises(InvalidReferenceTimeError, match="REFERENCE_TIME"):
        reference_now()


def test_result_is_always_utc_aware(reference):
    for value in ("", "seed", "2026-05-24T00:00:00Z", "2026-05-24T00:00:00"):
        reference(value)
        got = reference_now()
        assert got.tzinfo is not None
        assert got.utcoffset() == timedelta(0)


# ----- the regression guard ----------------------------------------------


def test_seed_dataset_never_decays_out_of_its_window():
    """Under the seed pin, every demo counterparty stays inside the window.

    This is the assertion that would have caught the original bug. It is
    date-independent: it holds today, and in ten years.
    """
    lookbacks = [
        r.lookback_days for r in POLICY.counterparty_rules
        if r.type == "recent_payment" and r.lookback_days is not None
    ]
    assert lookbacks, "policy has no recent_payment rule to guard"
    cutoff = SEED_REFERENCE_TIME - timedelta(days=max(lookbacks))

    in_window = [t for t in _TX_DATA if _ts(t[6]) >= cutoff]
    assert in_window, "no seeded transaction falls inside the counterparty window"

    # Each demo persona keeps a non-trivial counterparty graph — the symptom
    # of the original bug was user_b/user_c collapsing to their joint co-owner.
    for user_id in ("user_a", "user_b", "user_c"):
        assert len(seed_counterparty_set(user_id)) >= 3, (
            f"{user_id}'s counterparty graph has collapsed — the demo dataset "
            f"has aged out of its {max(lookbacks)}-day window"
        )


def test_window_is_closed_at_both_ends(reference):
    """A reference before the data sees nothing — the as-of upper bound.

    Without it, pinning the clock to replay a past decision would still surface
    transactions that had not happened yet at that instant.
    """
    reference("2020-01-01T00:00:00Z")
    for user_id in ("user_a", "user_b", "user_c"):
        assert _recent_only(user_id) == set()


def test_window_is_closed_at_the_stale_end(reference):
    """A reference long after the data sees nothing — relationships go stale."""
    reference("2030-01-01T00:00:00Z")
    for user_id in ("user_a", "user_b", "user_c"):
        assert _recent_only(user_id) == set()


def _recent_only(user_id: str) -> set[str]:
    """Just the `recent_payment` rule — joint co-ownership is clock-independent
    and would otherwise mask the window's behaviour."""
    from argo.db.seed import _seed_recent_payment

    lookback = next(
        r.lookback_days for r in POLICY.counterparty_rules
        if r.type == "recent_payment"
    )
    assert lookback is not None
    return _seed_recent_payment(user_id, lookback)


def test_wall_clock_would_have_decayed_the_demo(reference):
    """Documents the bug this module fixes, so the fix cannot be quietly undone.

    On wall clock the fixed seed dates fall outside the window (they already do
    as of mid-2026), which is precisely why the demo needs the pin. If this ever
    starts failing, the seed data has been regenerated relative to now — at
    which point the pin is no longer load-bearing and this file should be
    revisited.
    """
    reference("")
    wall = {u: seed_counterparty_set(u) for u in ("user_a", "user_b", "user_c")}
    reference("seed")
    pinned = {u: seed_counterparty_set(u) for u in ("user_a", "user_b", "user_c")}

    for user_id in wall:
        assert wall[user_id] <= pinned[user_id], (
            "wall clock should only ever see a subset of the pinned graph"
        )
    assert wall != pinned, (
        "seed data now looks recent on wall clock — the drift has been fixed "
        "by regenerating the dataset rather than by pinning; revisit this test"
    )
