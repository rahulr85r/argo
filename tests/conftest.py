"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from argo.config import settings


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the counterparty-rule clock to the seed dataset for every test.

    `recent_payment` is time-windowed, so on wall clock the expected-verdict
    tables in `test_entitlements.py` would decay as the fixed Feb–May 2026 seed
    slid out of the 90-day window — passing today, failing in a month, for no
    code change.

    `test_clock.py` covers the unpinned wall-clock path explicitly.
    """
    monkeypatch.setattr(settings, "reference_time", "seed")
