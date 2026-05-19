"""Phase 0 demo seed data: 3 users, 4 accounts, ~27 transactions.

Deterministic, dated relative to 2026-05-18 (PRD lock date) so the demo is
stable across runs. A↔C transfers are recorded twice (once per side) with
matched memos to support counterparty-role disambiguation later.
"""

from datetime import datetime, timezone

from argo.db import get_conn


USERS = [
    {"id": "user_a", "display_name": "Alice Chen", "email": "alice@example.com"},
    {"id": "user_b", "display_name": "Bob Patel", "email": "bob@example.com"},
    {"id": "user_c", "display_name": "Charlie Rivera", "email": "charlie@example.com"},
]

ACCOUNTS = [
    {"id": "acct_a_chk", "display_name": "Alice Checking",
     "account_type": "individual", "last4": "4421", "balance_cents": 425000},
    {"id": "acct_b_chk", "display_name": "Bob Checking",
     "account_type": "individual", "last4": "7782", "balance_cents": 1289000},
    {"id": "acct_c_chk", "display_name": "Charlie Checking",
     "account_type": "individual", "last4": "2014", "balance_cents": 137500},
    {"id": "acct_ab_joint", "display_name": "Joint Checking (Alice & Bob)",
     "account_type": "joint", "last4": "9933", "balance_cents": 850000},
]

OWNERSHIPS = [
    ("acct_a_chk", "user_a"),
    ("acct_b_chk", "user_b"),
    ("acct_c_chk", "user_c"),
    ("acct_ab_joint", "user_a"),
    ("acct_ab_joint", "user_b"),
]


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


TRANSACTIONS = [
    # ---- acct_a_chk (Alice's individual) ----
    {"id": "tx_a_001", "account_id": "acct_a_chk", "amount_cents": 320000,
     "direction": "inbound", "counterparty_name": "Acme Corp",
     "counterparty_user_id": None, "memo": "Paycheck — Apr",
     "ts": _ts("2026-04-15T09:00:00")},
    {"id": "tx_a_002", "account_id": "acct_a_chk", "amount_cents": 7500,
     "direction": "outbound", "counterparty_name": "Charlie Rivera",
     "counterparty_user_id": "user_c", "memo": "Rent split",
     "ts": _ts("2026-04-22T18:30:00")},
    {"id": "tx_a_003", "account_id": "acct_a_chk", "amount_cents": 12000,
     "direction": "inbound", "counterparty_name": "Charlie Rivera",
     "counterparty_user_id": "user_c", "memo": "Dinner repay",
     "ts": _ts("2026-04-29T20:15:00")},
    {"id": "tx_a_004", "account_id": "acct_a_chk", "amount_cents": 320000,
     "direction": "inbound", "counterparty_name": "Acme Corp",
     "counterparty_user_id": None, "memo": "Paycheck — May 1",
     "ts": _ts("2026-05-01T09:00:00")},
    {"id": "tx_a_005", "account_id": "acct_a_chk", "amount_cents": 5240,
     "direction": "outbound", "counterparty_name": "Whole Foods",
     "counterparty_user_id": None, "memo": "Groceries",
     "ts": _ts("2026-05-03T17:45:00")},
    {"id": "tx_a_006", "account_id": "acct_a_chk", "amount_cents": 25000,
     "direction": "outbound", "counterparty_name": "Charlie Rivera",
     "counterparty_user_id": "user_c", "memo": "Concert tickets",
     "ts": _ts("2026-05-05T13:20:00")},
    {"id": "tx_a_007", "account_id": "acct_a_chk", "amount_cents": 1875,
     "direction": "outbound", "counterparty_name": "Blue Bottle Coffee",
     "counterparty_user_id": None, "memo": "Coffee",
     "ts": _ts("2026-05-09T08:30:00")},
    {"id": "tx_a_008", "account_id": "acct_a_chk", "amount_cents": 4500,
     "direction": "outbound", "counterparty_name": "Charlie Rivera",
     "counterparty_user_id": "user_c", "memo": "Groceries share",
     "ts": _ts("2026-05-12T19:00:00")},
    {"id": "tx_a_009", "account_id": "acct_a_chk", "amount_cents": 30000,
     "direction": "inbound", "counterparty_name": "Charlie Rivera",
     "counterparty_user_id": "user_c", "memo": "Tahoe trip share",
     "ts": _ts("2026-05-15T11:00:00")},
    {"id": "tx_a_010", "account_id": "acct_a_chk", "amount_cents": 320000,
     "direction": "inbound", "counterparty_name": "Acme Corp",
     "counterparty_user_id": None, "memo": "Paycheck — May 15",
     "ts": _ts("2026-05-15T09:00:00")},

    # ---- acct_b_chk (Bob's individual) ----
    {"id": "tx_b_001", "account_id": "acct_b_chk", "amount_cents": 580000,
     "direction": "inbound", "counterparty_name": "Sierra Health",
     "counterparty_user_id": None, "memo": "Paycheck — Apr 30",
     "ts": _ts("2026-04-30T09:00:00")},
    {"id": "tx_b_002", "account_id": "acct_b_chk", "amount_cents": 42000,
     "direction": "outbound", "counterparty_name": "Larkspur Elementary",
     "counterparty_user_id": None, "memo": "Q4 school fees",
     "ts": _ts("2026-05-03T11:00:00")},
    {"id": "tx_b_003", "account_id": "acct_b_chk", "amount_cents": 8900,
     "direction": "outbound", "counterparty_name": "Equinox",
     "counterparty_user_id": None, "memo": "Gym — May",
     "ts": _ts("2026-05-08T07:00:00")},
    {"id": "tx_b_004", "account_id": "acct_b_chk", "amount_cents": 15600,
     "direction": "outbound", "counterparty_name": "Bay Area Dental",
     "counterparty_user_id": None, "memo": "Dental cleaning",
     "ts": _ts("2026-05-14T15:00:00")},
    {"id": "tx_b_005", "account_id": "acct_b_chk", "amount_cents": 580000,
     "direction": "inbound", "counterparty_name": "Sierra Health",
     "counterparty_user_id": None, "memo": "Paycheck — May 15",
     "ts": _ts("2026-05-15T09:00:00")},

    # ---- acct_c_chk (Charlie's individual; A↔C mirrors below) ----
    {"id": "tx_c_001", "account_id": "acct_c_chk", "amount_cents": 240000,
     "direction": "inbound", "counterparty_name": "Rivera Design",
     "counterparty_user_id": None, "memo": "Paycheck — Apr",
     "ts": _ts("2026-04-20T09:00:00")},
    {"id": "tx_c_002", "account_id": "acct_c_chk", "amount_cents": 7500,
     "direction": "inbound", "counterparty_name": "Alice Chen",
     "counterparty_user_id": "user_a", "memo": "Rent split",
     "ts": _ts("2026-04-22T18:30:00")},
    {"id": "tx_c_003", "account_id": "acct_c_chk", "amount_cents": 12000,
     "direction": "outbound", "counterparty_name": "Alice Chen",
     "counterparty_user_id": "user_a", "memo": "Dinner repay",
     "ts": _ts("2026-04-29T20:15:00")},
    {"id": "tx_c_004", "account_id": "acct_c_chk", "amount_cents": 240000,
     "direction": "inbound", "counterparty_name": "Rivera Design",
     "counterparty_user_id": None, "memo": "Paycheck — May",
     "ts": _ts("2026-05-04T09:00:00")},
    {"id": "tx_c_005", "account_id": "acct_c_chk", "amount_cents": 25000,
     "direction": "inbound", "counterparty_name": "Alice Chen",
     "counterparty_user_id": "user_a", "memo": "Concert tickets",
     "ts": _ts("2026-05-05T13:20:00")},
    {"id": "tx_c_006", "account_id": "acct_c_chk", "amount_cents": 8900,
     "direction": "outbound", "counterparty_name": "Adobe",
     "counterparty_user_id": None, "memo": "Creative Cloud",
     "ts": _ts("2026-05-08T10:00:00")},
    {"id": "tx_c_007", "account_id": "acct_c_chk", "amount_cents": 4500,
     "direction": "inbound", "counterparty_name": "Alice Chen",
     "counterparty_user_id": "user_a", "memo": "Groceries share",
     "ts": _ts("2026-05-12T19:00:00")},
    {"id": "tx_c_008", "account_id": "acct_c_chk", "amount_cents": 120000,
     "direction": "outbound", "counterparty_name": "Mission Properties",
     "counterparty_user_id": None, "memo": "Rent — May",
     "ts": _ts("2026-05-13T08:00:00")},
    {"id": "tx_c_009", "account_id": "acct_c_chk", "amount_cents": 30000,
     "direction": "outbound", "counterparty_name": "Alice Chen",
     "counterparty_user_id": "user_a", "memo": "Tahoe trip share",
     "ts": _ts("2026-05-15T11:00:00")},

    # ---- acct_ab_joint (Alice & Bob) ----
    {"id": "tx_j_001", "account_id": "acct_ab_joint", "amount_cents": 280000,
     "direction": "outbound", "counterparty_name": "Wells Fargo Mortgage",
     "counterparty_user_id": None, "memo": "Mortgage — May",
     "ts": _ts("2026-05-01T06:00:00")},
    {"id": "tx_j_002", "account_id": "acct_ab_joint", "amount_cents": 24500,
     "direction": "outbound", "counterparty_name": "PG&E",
     "counterparty_user_id": None, "memo": "Utilities — Apr",
     "ts": _ts("2026-05-02T10:00:00")},
    {"id": "tx_j_003", "account_id": "acct_ab_joint", "amount_cents": 18900,
     "direction": "outbound", "counterparty_name": "Comcast",
     "counterparty_user_id": None, "memo": "Internet — May",
     "ts": _ts("2026-05-05T10:00:00")},
    {"id": "tx_j_004", "account_id": "acct_ab_joint", "amount_cents": 31240,
     "direction": "outbound", "counterparty_name": "Trader Joe's",
     "counterparty_user_id": None, "memo": "Groceries",
     "ts": _ts("2026-05-12T17:00:00")},
    {"id": "tx_j_005", "account_id": "acct_ab_joint", "amount_cents": 18750,
     "direction": "outbound", "counterparty_name": "Trader Joe's",
     "counterparty_user_id": None, "memo": "Groceries",
     "ts": _ts("2026-05-16T16:30:00")},
]


def seed_if_empty() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM users")
            if cur.fetchone()["n"] > 0:
                return

            cur.executemany(
                "INSERT INTO users (id, display_name, email) "
                "VALUES (%(id)s, %(display_name)s, %(email)s)",
                USERS,
            )
            cur.executemany(
                "INSERT INTO accounts (id, display_name, account_type, last4, balance_cents) "
                "VALUES (%(id)s, %(display_name)s, %(account_type)s, %(last4)s, %(balance_cents)s)",
                ACCOUNTS,
            )
            cur.executemany(
                "INSERT INTO account_owners (account_id, user_id) VALUES (%s, %s)",
                OWNERSHIPS,
            )
            cur.executemany(
                "INSERT INTO transactions "
                "(id, account_id, amount_cents, direction, counterparty_name, "
                "counterparty_user_id, memo, ts) "
                "VALUES (%(id)s, %(account_id)s, %(amount_cents)s, %(direction)s, "
                "%(counterparty_name)s, %(counterparty_user_id)s, %(memo)s, %(ts)s)",
                TRANSACTIONS,
            )
        conn.commit()
