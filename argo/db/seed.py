"""Phase 0 seed: 3 users, 4 accounts, ~100 transactions spanning Feb–May 2026.

Designed to create realistic ambiguity for the LLM:
  - All cross-user flow directions present: A↔B, A↔C, B↔C, A↔AB, B↔AB, C↔AB
  - Misc external vendors create noise
  - Several vendors deliberately share surnames with users to stress entity resolution
    (e.g. "Rivera Studios" vs user Charlie Rivera). The claim extractor must
    use counterparty_user_id (the FK link), not name matching, to attribute claims.

Idempotent: only inserts if the users table is empty.
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


# (account, amount_cents, direction, counterparty_name, counterparty_user_id, memo, ts_iso)
# Sorted chronologically. Cross-user transfers recorded twice (once per side)
# with matched memos and timestamps.
_TX_DATA: list[tuple] = [
    # ===== FEBRUARY =====
    ("acct_ab_joint", 280000, "outbound", "Wells Fargo Mortgage", None, "Mortgage — Feb", "2026-02-01T06:00:00"),
    ("acct_ab_joint", 23800, "outbound", "PG&E", None, "Utilities — Jan", "2026-02-02T10:00:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Feb 4", "2026-02-04T09:00:00"),
    ("acct_ab_joint", 18900, "outbound", "Comcast", None, "Internet — Feb", "2026-02-05T10:00:00"),
    ("acct_a_chk", 1099, "outbound", "Spotify", None, "Subscription", "2026-02-05T08:00:00"),
    ("acct_a_chk", 6740, "outbound", "Whole Foods", None, "Groceries", "2026-02-12T18:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — Feb 15", "2026-02-15T09:00:00"),
    # Confusion: "Chen's Tea House" is external — not user_a Alice Chen
    ("acct_a_chk", 4250, "outbound", "Chen's Tea House", None, "Tea + scones", "2026-02-18T15:00:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Feb 20", "2026-02-20T09:00:00"),
    # A→C hiking
    ("acct_a_chk", 9000, "outbound", "Charlie Rivera", "user_c", "Hiking trip share", "2026-02-25T14:00:00"),
    ("acct_c_chk", 9000, "inbound", "Alice Chen", "user_a", "Hiking trip share", "2026-02-25T14:00:00"),
    ("acct_b_chk", 580000, "inbound", "Sierra Health", None, "Paycheck — Feb 28", "2026-02-28T09:00:00"),
    # A→AB top-up
    ("acct_a_chk", 150000, "outbound", "Joint Checking (Alice & Bob)", None, "Top-up", "2026-02-28T18:00:00"),
    ("acct_ab_joint", 150000, "inbound", "Alice Chen", "user_a", "Top-up from Alice", "2026-02-28T18:00:00"),
    # B→AB top-up
    ("acct_b_chk", 150000, "outbound", "Joint Checking (Alice & Bob)", None, "Top-up", "2026-02-28T18:30:00"),
    ("acct_ab_joint", 150000, "inbound", "Bob Patel", "user_b", "Top-up from Bob", "2026-02-28T18:30:00"),

    # ===== MARCH =====
    ("acct_ab_joint", 280000, "outbound", "Wells Fargo Mortgage", None, "Mortgage — Mar", "2026-03-01T06:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — Mar 1", "2026-03-01T09:00:00"),
    ("acct_ab_joint", 26100, "outbound", "PG&E", None, "Utilities — Feb", "2026-03-02T10:00:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Mar 4", "2026-03-04T09:00:00"),
    # B→C logo commission
    ("acct_b_chk", 20000, "outbound", "Charlie Rivera", "user_c", "Logo design commission", "2026-03-05T11:00:00"),
    ("acct_c_chk", 20000, "inbound", "Bob Patel", "user_b", "Logo design commission", "2026-03-05T11:00:00"),
    ("acct_ab_joint", 18900, "outbound", "Comcast", None, "Internet — Mar", "2026-03-05T10:00:00"),
    ("acct_ab_joint", 27840, "outbound", "Trader Joe's", None, "Groceries", "2026-03-08T17:00:00"),
    # A→C coffee meetup
    ("acct_a_chk", 4000, "outbound", "Charlie Rivera", "user_c", "Coffee meetup", "2026-03-12T10:00:00"),
    ("acct_c_chk", 4000, "inbound", "Alice Chen", "user_a", "Coffee meetup", "2026-03-12T10:00:00"),
    # A→B vintage chair
    ("acct_a_chk", 20000, "outbound", "Bob Patel", "user_b", "Vintage chair pickup", "2026-03-15T14:00:00"),
    ("acct_b_chk", 20000, "inbound", "Alice Chen", "user_a", "Vintage chair pickup", "2026-03-15T14:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — Mar 15", "2026-03-15T09:00:00"),
    # Confusion: "Patel Auto Repair" is external — not user_b Bob Patel
    ("acct_b_chk", 32000, "outbound", "Patel Auto Repair", None, "Brake pads", "2026-03-18T11:00:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Mar 20", "2026-03-20T09:00:00"),
    # B→A coffee Sunday
    ("acct_b_chk", 12000, "outbound", "Alice Chen", "user_a", "Coffee Sunday group", "2026-03-20T11:00:00"),
    ("acct_a_chk", 12000, "inbound", "Bob Patel", "user_b", "Coffee Sunday group", "2026-03-20T11:00:00"),
    ("acct_b_chk", 12400, "outbound", "Patagonia", None, "Hiking jacket", "2026-03-22T13:00:00"),
    # C→A photo prints
    ("acct_c_chk", 6000, "outbound", "Alice Chen", "user_a", "Photo prints", "2026-03-22T16:00:00"),
    ("acct_a_chk", 6000, "inbound", "Charlie Rivera", "user_c", "Photo prints", "2026-03-22T16:00:00"),
    ("acct_c_chk", 9800, "outbound", "Lululemon", None, "Yoga gear", "2026-03-25T14:00:00"),
    ("acct_a_chk", 7850, "outbound", "Trader Joe's", None, "Groceries", "2026-03-28T18:00:00"),
    ("acct_b_chk", 580000, "inbound", "Sierra Health", None, "Paycheck — Mar 30", "2026-03-30T09:00:00"),

    # ===== APRIL =====
    ("acct_ab_joint", 280000, "outbound", "Wells Fargo Mortgage", None, "Mortgage — Apr", "2026-04-01T06:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — Apr 1", "2026-04-01T09:00:00"),
    ("acct_ab_joint", 25400, "outbound", "PG&E", None, "Utilities — Mar", "2026-04-02T10:00:00"),
    ("acct_b_chk", 8900, "outbound", "Costco", None, "Bulk groceries", "2026-04-03T12:00:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Apr 4", "2026-04-04T09:00:00"),
    # C→AB trip share to joint
    ("acct_c_chk", 25000, "outbound", "Joint Checking (Alice & Bob)", None, "Trip share to joint", "2026-04-05T11:00:00"),
    ("acct_ab_joint", 25000, "inbound", "Charlie Rivera", "user_c", "Trip share contribution", "2026-04-05T11:00:00"),
    ("acct_ab_joint", 18900, "outbound", "Comcast", None, "Internet — Apr", "2026-04-05T10:00:00"),
    # Confusion: "Bob's Burritos" external — not user_b
    ("acct_a_chk", 3400, "outbound", "Bob's Burritos", None, "Lunch", "2026-04-08T13:00:00"),
    # A→B birthday gift
    ("acct_a_chk", 8500, "outbound", "Bob Patel", "user_b", "Birthday gift", "2026-04-10T16:00:00"),
    ("acct_b_chk", 8500, "inbound", "Alice Chen", "user_a", "Birthday gift", "2026-04-10T16:00:00"),
    ("acct_b_chk", 24500, "outbound", "Best Buy", None, "Headphones", "2026-04-11T15:00:00"),
    # C→B photo editing
    ("acct_c_chk", 7500, "outbound", "Bob Patel", "user_b", "Photo editing service", "2026-04-12T14:00:00"),
    ("acct_b_chk", 7500, "inbound", "Charlie Rivera", "user_c", "Photo editing service", "2026-04-12T14:00:00"),
    ("acct_ab_joint", 29840, "outbound", "Trader Joe's", None, "Groceries", "2026-04-14T17:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — Apr 15", "2026-04-15T09:00:00"),
    ("acct_c_chk", 4200, "outbound", "Apple", None, "App Store", "2026-04-16T11:00:00"),
    ("acct_a_chk", 4599, "outbound", "Amazon", None, "Books + cables", "2026-04-18T20:00:00"),
    ("acct_a_chk", 7830, "outbound", "Sushi Ya", None, "Dinner", "2026-04-20T19:30:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — Apr 20", "2026-04-20T09:00:00"),
    # A→C rent split
    ("acct_a_chk", 7500, "outbound", "Charlie Rivera", "user_c", "Rent split", "2026-04-22T18:30:00"),
    ("acct_c_chk", 7500, "inbound", "Alice Chen", "user_a", "Rent split", "2026-04-22T18:30:00"),
    # A→AB top-up for utilities
    ("acct_a_chk", 150000, "outbound", "Joint Checking (Alice & Bob)", None, "Top-up for utilities", "2026-04-28T18:00:00"),
    ("acct_ab_joint", 150000, "inbound", "Alice Chen", "user_a", "Top-up from Alice", "2026-04-28T18:00:00"),
    # B→AB top-up for utilities
    ("acct_b_chk", 150000, "outbound", "Joint Checking (Alice & Bob)", None, "Top-up for utilities", "2026-04-28T18:30:00"),
    ("acct_ab_joint", 150000, "inbound", "Bob Patel", "user_b", "Top-up from Bob", "2026-04-28T18:30:00"),
    # C→A dinner repay
    ("acct_c_chk", 12000, "outbound", "Alice Chen", "user_a", "Dinner repay", "2026-04-29T20:15:00"),
    ("acct_a_chk", 12000, "inbound", "Charlie Rivera", "user_c", "Dinner repay", "2026-04-29T20:15:00"),
    ("acct_b_chk", 580000, "inbound", "Sierra Health", None, "Paycheck — Apr 30", "2026-04-30T09:00:00"),
    # C→B custom illustration
    ("acct_c_chk", 15000, "outbound", "Bob Patel", "user_b", "Custom illustration", "2026-04-30T15:00:00"),
    ("acct_b_chk", 15000, "inbound", "Charlie Rivera", "user_c", "Custom illustration", "2026-04-30T15:00:00"),

    # ===== MAY =====
    ("acct_ab_joint", 280000, "outbound", "Wells Fargo Mortgage", None, "Mortgage — May", "2026-05-01T06:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — May 1", "2026-05-01T09:00:00"),
    ("acct_ab_joint", 24500, "outbound", "PG&E", None, "Utilities — Apr", "2026-05-02T10:00:00"),
    ("acct_a_chk", 5240, "outbound", "Whole Foods", None, "Groceries", "2026-05-03T17:45:00"),
    ("acct_c_chk", 240000, "inbound", "Rivera Design", None, "Paycheck — May", "2026-05-04T09:00:00"),
    ("acct_a_chk", 2350, "outbound", "Uber", None, "Ride", "2026-05-04T22:30:00"),
    # A→C concert tickets
    ("acct_a_chk", 25000, "outbound", "Charlie Rivera", "user_c", "Concert tickets", "2026-05-05T13:20:00"),
    ("acct_c_chk", 25000, "inbound", "Alice Chen", "user_a", "Concert tickets", "2026-05-05T13:20:00"),
    ("acct_ab_joint", 18900, "outbound", "Comcast", None, "Internet — May", "2026-05-05T10:00:00"),
    # B→A lunch tab
    ("acct_b_chk", 5000, "outbound", "Alice Chen", "user_a", "Settling lunch tab", "2026-05-07T13:00:00"),
    ("acct_a_chk", 5000, "inbound", "Bob Patel", "user_b", "Settling lunch tab", "2026-05-07T13:00:00"),
    ("acct_b_chk", 8900, "outbound", "Equinox", None, "Gym — May", "2026-05-08T07:00:00"),
    ("acct_c_chk", 8900, "outbound", "Adobe", None, "Creative Cloud", "2026-05-08T10:00:00"),
    # AB→C dinner refund
    ("acct_ab_joint", 12000, "outbound", "Charlie Rivera", "user_c", "Shared dinner refund", "2026-05-08T20:00:00"),
    ("acct_c_chk", 12000, "inbound", "Joint Checking (Alice & Bob)", None, "Shared dinner refund from joint", "2026-05-08T20:00:00"),
    # B→C print order
    ("acct_b_chk", 6000, "outbound", "Charlie Rivera", "user_c", "Print order", "2026-05-09T11:00:00"),
    ("acct_c_chk", 6000, "inbound", "Bob Patel", "user_b", "Print order", "2026-05-09T11:00:00"),
    ("acct_a_chk", 1875, "outbound", "Blue Bottle Coffee", None, "Coffee", "2026-05-09T08:30:00"),
    ("acct_c_chk", 1800, "outbound", "Tartine", None, "Pastries", "2026-05-10T08:00:00"),
    # A→AB monthly contribution
    ("acct_a_chk", 80000, "outbound", "Joint Checking (Alice & Bob)", None, "Monthly contribution", "2026-05-10T18:00:00"),
    ("acct_ab_joint", 80000, "inbound", "Alice Chen", "user_a", "Monthly contribution from Alice", "2026-05-10T18:00:00"),
    # B→AB monthly contribution
    ("acct_b_chk", 80000, "outbound", "Joint Checking (Alice & Bob)", None, "Monthly contribution", "2026-05-10T18:30:00"),
    ("acct_ab_joint", 80000, "inbound", "Bob Patel", "user_b", "Monthly contribution from Bob", "2026-05-10T18:30:00"),
    ("acct_a_chk", 11200, "outbound", "Target", None, "Home goods", "2026-05-11T16:00:00"),
    # A→C groceries share
    ("acct_a_chk", 4500, "outbound", "Charlie Rivera", "user_c", "Groceries share", "2026-05-12T19:00:00"),
    ("acct_c_chk", 4500, "inbound", "Alice Chen", "user_a", "Groceries share", "2026-05-12T19:00:00"),
    ("acct_ab_joint", 31240, "outbound", "Trader Joe's", None, "Groceries", "2026-05-12T17:00:00"),
    ("acct_c_chk", 120000, "outbound", "Mission Properties", None, "Rent — May", "2026-05-13T08:00:00"),
    ("acct_b_chk", 15600, "outbound", "Bay Area Dental", None, "Dental cleaning", "2026-05-14T15:00:00"),
    ("acct_a_chk", 320000, "inbound", "Acme Corp", None, "Paycheck — May 15", "2026-05-15T09:00:00"),
    ("acct_b_chk", 580000, "inbound", "Sierra Health", None, "Paycheck — May 15", "2026-05-15T09:00:00"),
    # C→A Tahoe trip share
    ("acct_c_chk", 30000, "outbound", "Alice Chen", "user_a", "Tahoe trip share", "2026-05-15T11:00:00"),
    ("acct_a_chk", 30000, "inbound", "Charlie Rivera", "user_c", "Tahoe trip share", "2026-05-15T11:00:00"),
    # Confusion: "Charlie's Auto Wash" external — not user_c
    ("acct_b_chk", 2500, "outbound", "Charlie's Auto Wash", None, "Car wash", "2026-05-16T12:00:00"),
    ("acct_a_chk", 18900, "outbound", "REI", None, "Camping gear", "2026-05-16T15:00:00"),
    ("acct_ab_joint", 18750, "outbound", "Trader Joe's", None, "Groceries", "2026-05-16T16:30:00"),
    # Confusion: "Alice Pharmacy" external — not user_a
    ("acct_a_chk", 1850, "inbound", "Alice Pharmacy", None, "Prescription refund", "2026-05-17T09:00:00"),
    # Confusion: "Rivera Studios" external — not user_c
    ("acct_b_chk", 25000, "outbound", "Rivera Studios", None, "Headshot session", "2026-05-17T14:00:00"),
]


TRANSACTIONS = [
    {
        "id": f"tx_{i:03d}",
        "account_id": account_id,
        "amount_cents": amount_cents,
        "direction": direction,
        "counterparty_name": counterparty_name,
        "counterparty_user_id": counterparty_user_id,
        "memo": memo,
        "ts": _ts(ts_iso),
    }
    for i, (account_id, amount_cents, direction, counterparty_name,
            counterparty_user_id, memo, ts_iso) in enumerate(_TX_DATA, start=1)
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
