"""Shared fixtures: run the full gating pipeline with no network and no Postgres.

Argo resolves its five pluggable seams into module-level singletons (some at
import time, some lazily on first use). That is the right shape for a gateway
process — one adapter, reused across requests — but it means tests have to
install their doubles over those singletons rather than passing them in.

Each fixture below does exactly that for one seam, via monkeypatch so the
substitution is undone after the test:

    argo.llm._CLIENT               → FakeLlmClient        (no LLM provider)
    argo.pipeline._ENTITLEMENT_ADAPTER → SeedDerivedAdapter   (no Postgres)
    argo.verifier._VERIFIER        → LlmVerifier + SeedTransactionSource
    argo.db.audit._WRITER          → InMemoryAuditWriter
    argo.judge._render_known_entities → static seed roster (no Postgres)

`gated` is the one most tests want: it composes all five and returns a
callable with the same signature as `gate_response()`.

The one exception is `live_db`, which does the opposite: it points Argo at a
real Postgres so the *production* adapters can be exercised. It skips unless
`TEST_DATABASE_URL` is set, so the default `pytest` run stays offline.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

from argo.config import settings
from argo.db.audit import InMemoryAuditWriter
from argo.llm import FakeLlmClient

# The `db` marker is declared in pyproject.toml under
# [tool.pytest.ini_options] so `--strict-markers` recognises it.


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the counterparty-rule clock to the seed dataset for every test.

    `recent_payment` is time-windowed, so on wall clock the expected-verdict
    tables in `test_entitlements.py` would decay as the fixed Feb–May 2026 seed
    slid out of the 90-day window — passing today, failing in a month, for no
    code change. Autouse because it applies to the offline and live-Postgres
    suites alike: both adapters read `argo.clock.reference_now()`, so pinning
    here is what makes them comparable at all.

    `test_clock.py` covers the unpinned wall-clock path explicitly.
    """
    monkeypatch.setattr(settings, "reference_time", "seed")

LABELED = {
    ex["id"]: ex
    for ex in json.loads(
        (Path(__file__).resolve().parent.parent / "eval" / "labeled_claims.json").read_text()
    )
}


# ----- known-entities block (normally rendered from Postgres) ------------

# Mirrors argo.judge._render_known_entities() but sourced from the in-memory
# seed, so the extractor prompt is realistic without a database.
def _seed_known_entities() -> str:
    from argo.db.seed import ACCOUNTS, OWNERSHIPS, USERS

    lines = ["USERS:"]
    lines += [f"- {u['id']}: {u['display_name']}, {u['email']}" for u in USERS]
    lines += ["", "ACCOUNTS:"]
    for a in ACCOUNTS:
        owners = " + ".join(uid for (acct, uid) in OWNERSHIPS if acct == a["id"])
        lines.append(
            f"- {a['id']}: {a['display_name']} (ending {a['last4']}), "
            f"{a['account_type']}, owned by {owners}"
        )
    return "\n".join(lines)


# ----- individual seam fixtures ------------------------------------------


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeLlmClient]:
    """Install a scripted `FakeLlmClient` as the active LLM client.

    Returns a factory so the test can script responses after the fixture is
    requested:  `client = fake_llm(judge='{"claims": []}')`.
    """

    def install(**kwargs) -> FakeLlmClient:
        client = FakeLlmClient(**kwargs)
        monkeypatch.setattr("argo.llm._CLIENT", client)
        return client

    return install


@pytest.fixture
def audit_sink(monkeypatch: pytest.MonkeyPatch) -> Callable[..., InMemoryAuditWriter]:
    """Install an `InMemoryAuditWriter`. Pass `fail=True` to simulate an outage."""

    def install(*, fail: bool = False) -> InMemoryAuditWriter:
        writer = InMemoryAuditWriter(fail=fail)
        monkeypatch.setattr("argo.db.audit._WRITER", writer)
        return writer

    return install


@pytest.fixture
def seed_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the entitlement adapter, verifier, and extractor prompt at the seed."""
    from argo.db.seed import SeedTransactionSource
    from argo.entitlements import SeedDerivedAdapter
    from argo.verifier import LlmVerifier

    monkeypatch.setattr("argo.pipeline._ENTITLEMENT_ADAPTER", SeedDerivedAdapter())

    verifier = LlmVerifier()
    verifier._source = SeedTransactionSource()
    monkeypatch.setattr("argo.verifier._VERIFIER", verifier)

    known = _seed_known_entities()
    monkeypatch.setattr("argo.judge._render_known_entities", lambda: known)


@pytest.fixture
def gated(
    seed_backends: None,
    fake_llm: Callable[..., FakeLlmClient],
    audit_sink: Callable[..., InMemoryAuditWriter],
) -> Iterator[Callable[..., object]]:
    """Full offline pipeline. Yields a `run(...)` helper.

    Usage:
        result, llm, audit = run(
            user_id="user_a",
            raw_response="...",
            judge=['{"claims": [...]}'],   # scripted judge responses, in order
        )
    """
    from argo.pipeline import gate_response

    def run(
        *,
        user_id: str = "user_a",
        query: str = "test query",
        raw_response: str,
        judge=None,
        audit_fails: bool = False,
    ):
        client = fake_llm(judge=judge) if judge is not None else fake_llm()
        writer = audit_sink(fail=audit_fails)
        result = gate_response(user_id=user_id, query=query, raw_response=raw_response)
        return result, client, writer

    yield run


# ----- live Postgres (the production adapters) ---------------------------


@pytest.fixture(scope="session")
def live_db() -> Iterator[None]:
    """Point `settings` at TEST_DATABASE_URL and apply schema + seed.

    Skips the whole test when the env var is absent, so `pytest` with no
    infrastructure still runs the offline suite. CI sets it to a Postgres
    service container — see `.github/workflows/ci.yml`.

    Session-scoped: schema creation and the ~500-row seed are idempotent but
    slow enough to be worth doing once. Uses an explicit `MonkeyPatch` because
    the built-in fixture is function-scoped.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        # CI sets ARGO_REQUIRE_DB=1 so a misconfigured service container fails
        # the build loudly instead of silently skipping the only tests that
        # cover the production data path.
        if os.environ.get("ARGO_REQUIRE_DB"):
            pytest.fail(
                "ARGO_REQUIRE_DB is set but TEST_DATABASE_URL is not — "
                "the live-Postgres tests would have silently skipped.",
                pytrace=False,
            )
        pytest.skip("TEST_DATABASE_URL not set — skipping live-Postgres tests")

    parsed = urlparse(url)
    mp = pytest.MonkeyPatch()
    from argo.config import settings

    mp.setattr(settings, "postgres_host", parsed.hostname or "localhost")
    mp.setattr(settings, "postgres_port", parsed.port or 5432)
    mp.setattr(settings, "postgres_user", parsed.username or "argo")
    mp.setattr(settings, "postgres_password", parsed.password or "argo")
    mp.setattr(settings, "postgres_db", (parsed.path or "/argo").lstrip("/"))

    from argo.db.bootstrap import init_db

    init_db()
    try:
        yield
    finally:
        mp.undo()
