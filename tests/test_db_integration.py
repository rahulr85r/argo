"""Live-Postgres tests for the production data layer.

Everything here is skipped unless `TEST_DATABASE_URL` is set, so the default
offline suite is unaffected. CI provides a Postgres service container.

These cover what the offline suite structurally cannot:

  - `DbDerivedAdapter` — the adapter that actually ships. The offline tests all
    run through `SeedDerivedAdapter`, so without this file the production
    entitlement path is never executed by a test.
  - `argo/db/queries.py` — the four SQL helpers behind the counterparty rules.
  - `PostgresAuditWriter` — that an audit row survives a real round-trip.
  - `PostgresTransactionSource` — the verifier's real data source.

The headline is `test_adapters_agree_on_owned_subjects`: the two adapters are
supposed to apply the *same* POLICY through different data sources, and nothing
proved that before.
"""

from __future__ import annotations

import pytest

from argo.db.audit import (
    AuditedClaim,
    AuditEvent,
    PostgresAuditWriter,
    get_recent_audit_events,
)
from argo.db.queries import (
    PostgresTransactionSource,
    get_account_ids_for_user,
    get_all_accounts_with_owners,
    get_all_users,
    get_joint_account_co_owners,
    get_recent_payment_counterparties,
    get_user,
)
from argo.db.seed import SeedTransactionSource, accounts_for
from argo.entitlements import DbDerivedAdapter, SeedDerivedAdapter, UnknownUserError

pytestmark = pytest.mark.db

USERS = ("user_a", "user_b", "user_c")


# ----- adapter parity: the gap this file exists to close ------------------


@pytest.mark.parametrize("user_id", USERS)
def test_adapters_agree_on_owned_subjects(live_db, user_id):
    """Ownership is time-independent, so the two adapters must match exactly.

    A divergence here means the production path grants or withholds access
    that the entire offline policy suite never tested.
    """
    db = DbDerivedAdapter().get_bundle(user_id)
    seed = SeedDerivedAdapter().get_bundle(user_id)

    assert db.owned_subjects == seed.owned_subjects
    assert db.user_id == seed.user_id


@pytest.mark.parametrize("user_id", USERS)
def test_adapters_agree_on_the_field_whitelist(live_db, user_id):
    """Both adapters read `counterparty_fields` from the same POLICY singleton."""
    db = DbDerivedAdapter().get_bundle(user_id)
    seed = SeedDerivedAdapter().get_bundle(user_id)
    assert db.counterparty_fields == seed.counterparty_fields


@pytest.mark.parametrize("user_id", USERS)
def test_adapters_agree_on_counterparties(live_db, user_id):
    """Exact equality — the two evaluators now share one clock.

    This used to be a subset assertion, because `argo/db/queries.py` anchored
    its window to SQL `NOW()` while the seed evaluator pinned itself to the
    newest seeded transaction. Both now read `argo.clock.reference_now()`, and
    the autouse `pinned_clock` fixture sets REFERENCE_TIME=seed, so a
    divergence here means the two implementations of `recent_payment` have
    genuinely drifted apart rather than merely disagreeing about the date.
    """
    db = DbDerivedAdapter().get_bundle(user_id)
    seed = SeedDerivedAdapter().get_bundle(user_id)
    assert db.counterparty_visible == seed.counterparty_visible


@pytest.mark.parametrize("user_id", USERS)
def test_adapters_agree_on_the_whole_bundle(live_db, user_id):
    """The strongest statement of the parity guarantee: identical bundles."""
    assert DbDerivedAdapter().get_bundle(user_id) == SeedDerivedAdapter().get_bundle(user_id)


def test_sql_window_honours_an_explicit_pin(live_db, monkeypatch):
    """The SQL window tracks `reference_now()`, not the database's clock.

    Both ends are checked. A reference *before* the data empties the window via
    the upper bound (an as-of replay must not see transactions that had not
    happened yet); a reference long *after* it empties the window via the
    lower bound (the relationships have gone stale). If the query reverted to
    `NOW()`, neither would hold.
    """
    from argo.config import settings

    monkeypatch.setattr(settings, "reference_time", "2020-01-01T00:00:00Z")
    assert get_recent_payment_counterparties("user_a", 90) == [], "upper bound not applied"

    monkeypatch.setattr(settings, "reference_time", "2030-01-01T00:00:00Z")
    assert get_recent_payment_counterparties("user_a", 90) == [], "lower bound not applied"

    monkeypatch.setattr(settings, "reference_time", "seed")
    assert get_recent_payment_counterparties("user_a", 90)


def test_joint_co_owners_are_always_counterparties(live_db):
    """The clock-independent half of the counterparty graph, asserted exactly.

    user_a and user_b co-own acct_ab_joint, so each must see the other
    regardless of transaction recency.
    """
    assert "user_b" in DbDerivedAdapter().get_bundle("user_a").counterparty_visible
    assert "user_a" in DbDerivedAdapter().get_bundle("user_b").counterparty_visible


def test_non_counterparty_users_stay_out_of_the_bundle(live_db):
    """The 'wall' the seed builds: eight users have no link to user_a."""
    visible = DbDerivedAdapter().get_bundle("user_a").counterparty_visible
    for stranger in ("user_h", "user_i", "user_k", "user_n"):
        assert stranger not in visible


def test_user_is_never_their_own_counterparty(live_db):
    for user_id in USERS:
        bundle = DbDerivedAdapter().get_bundle(user_id)
        assert user_id not in bundle.counterparty_visible


def test_unknown_user_raises(live_db):
    with pytest.raises(UnknownUserError):
        DbDerivedAdapter().get_bundle("user_does_not_exist")


# ----- the TTL cache -----------------------------------------------------


def test_bundle_cache_returns_the_same_object(live_db):
    adapter = DbDerivedAdapter()
    assert adapter.get_bundle("user_a") is adapter.get_bundle("user_a")


def test_invalidate_forces_a_refetch(live_db):
    adapter = DbDerivedAdapter()
    first = adapter.get_bundle("user_a")
    adapter.invalidate("user_a")
    second = adapter.get_bundle("user_a")
    assert first is not second
    assert first == second  # frozen dataclass — equal by value


def test_invalidate_all_clears_every_user(live_db):
    adapter = DbDerivedAdapter()
    a, b = adapter.get_bundle("user_a"), adapter.get_bundle("user_b")
    adapter.invalidate()
    assert adapter.get_bundle("user_a") is not a
    assert adapter.get_bundle("user_b") is not b


def test_expired_entry_is_refetched(live_db):
    adapter = DbDerivedAdapter(ttl_seconds=0.0)
    assert adapter.get_bundle("user_a") is not adapter.get_bundle("user_a")


# ----- the SQL helpers ---------------------------------------------------


def test_get_user_round_trip(live_db):
    user = get_user("user_a")
    assert user is not None
    assert user["id"] == "user_a"
    assert user["display_name"] == "Alice Chen"
    assert get_user("user_does_not_exist") is None


@pytest.mark.parametrize("user_id", USERS)
def test_account_ids_match_the_seed(live_db, user_id):
    assert set(get_account_ids_for_user(user_id)) == accounts_for(user_id)


def test_joint_account_shows_both_owners(live_db):
    accounts = {a["id"]: a for a in get_all_accounts_with_owners()}
    assert set(accounts["acct_ab_joint"]["owner_ids"]) == {"user_a", "user_b"}


def test_all_users_present(live_db):
    ids = {u["id"] for u in get_all_users()}
    assert set(USERS) <= ids
    assert len(ids) >= 20  # seed ships 26 users + establishments


def test_joint_co_owner_query_is_symmetric(live_db):
    assert "user_b" in get_joint_account_co_owners("user_a")
    assert "user_a" in get_joint_account_co_owners("user_b")
    assert "user_a" not in get_joint_account_co_owners("user_a")


def test_recent_payment_lookback_widens_monotonically(live_db):
    """A longer window can only add counterparties, never remove them."""
    narrow = set(get_recent_payment_counterparties("user_a", 1))
    wide = set(get_recent_payment_counterparties("user_a", 3650))
    assert narrow <= wide
    assert wide, "a 10-year window should find some counterparties"
    assert "user_a" not in wide


# ----- PostgresTransactionSource -----------------------------------------


@pytest.mark.parametrize("user_id", USERS)
def test_transaction_source_matches_the_seed_source(live_db, user_id):
    """The verifier must see identical rows whichever source is wired.

    Compared on the fields the verifier's prompt renderer actually reads.
    """
    def key(rows):
        return sorted(
            (r["id"], r["account_id"], r["amount_cents"], r["direction"],
             r["counterparty_name"], r["counterparty_user_id"], r["memo"])
            for r in rows
        )

    assert key(PostgresTransactionSource().get_user_transactions(user_id)) == key(
        SeedTransactionSource().get_user_transactions(user_id)
    )


@pytest.mark.parametrize("user_id", USERS)
def test_transaction_source_is_scoped_to_owned_accounts(live_db, user_id):
    """The scoping guarantee, asserted against real SQL this time."""
    rows = PostgresTransactionSource().get_user_transactions(user_id)
    assert rows
    assert {r["account_id"] for r in rows} <= accounts_for(user_id)


def test_joint_account_rows_visible_to_both_owners(live_db):
    def joint_ids(user_id):
        return {
            r["id"] for r in PostgresTransactionSource().get_user_transactions(user_id)
            if r["account_id"] == "acct_ab_joint"
        }

    assert joint_ids("user_a") == joint_ids("user_b")
    assert joint_ids("user_a"), "joint account should carry transactions"


# ----- PostgresAuditWriter -----------------------------------------------


def test_audit_event_round_trips(live_db):
    event = AuditEvent(
        user_id="user_a",
        query="integration-test query",
        raw_response="Bob's balance is $12,890.00.",
        final_response="[redacted]",
        whole_blocked=False,
        redacted_chars=28,
        claim_audit=[
            AuditedClaim(
                text="Bob's balance", subject="acct_b_chk", type="balance",
                role="primary", source_span="Bob's balance is $12,890.00",
                verdict="BLOCK", reason="account not owned by user_a",
            )
        ],
        chat_model="test-model",
        chat_latency_ms=11,
        extractor_latency_ms=22,
        verifier_latency_ms=33,
    )
    audit_id = PostgresAuditWriter().write(event)
    assert isinstance(audit_id, int)

    stored = next(
        e for e in get_recent_audit_events("user_a", limit=50) if e.id == audit_id
    )
    assert stored.query == "integration-test query"
    assert stored.final_response == "[redacted]"
    assert stored.redacted_chars == 28
    assert stored.chat_model == "test-model"
    assert (stored.chat_latency_ms, stored.extractor_latency_ms,
            stored.verifier_latency_ms) == (11, 22, 33)

    # The per-claim trail survives the jsonb round-trip intact.
    assert len(stored.claim_audit) == 1
    claim = stored.claim_audit[0]
    assert claim.verdict == "BLOCK"
    assert claim.subject == "acct_b_chk"
    assert claim.reason == "account not owned by user_a"


def test_audit_query_filters_by_user(live_db):
    writer = PostgresAuditWriter()
    for uid in ("user_a", "user_b"):
        writer.write(
            AuditEvent(
                user_id=uid, query=f"filter probe {uid}",
                raw_response="x", final_response="x",
                whole_blocked=False, chat_model="test-model",
            )
        )

    events = get_recent_audit_events("user_b", limit=100)
    assert events
    assert {e.user_id for e in events} == {"user_b"}


def test_audit_events_are_newest_first(live_db):
    writer = PostgresAuditWriter()
    for i in range(3):
        writer.write(
            AuditEvent(
                user_id="user_c", query=f"ordering probe {i}",
                raw_response="x", final_response="x",
                whole_blocked=False, chat_model="test-model",
            )
        )

    events = get_recent_audit_events("user_c", limit=3)
    assert [e.ts for e in events] == sorted((e.ts for e in events), reverse=True)


# ----- schema bootstrap --------------------------------------------------


def test_init_db_is_idempotent(live_db):
    """`init_db()` runs on every gateway start — a second call must be a no-op."""
    from argo.db.bootstrap import init_db

    before = len(get_all_users())
    init_db()
    assert len(get_all_users()) == before
