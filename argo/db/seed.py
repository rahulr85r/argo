"""Demo seed: 26 users (20 people + 6 establishments) with rich tx history.

User A (Alice Chen) is the exploratory persona. Her ~120 transactions span
Feb–May 2026 and include:
  - bi-weekly paycheck from user_z (Acme Corp Payroll)
  - weekly Starbucks (user_g), Whole Foods (user_l), biweekly Shell (user_r)
  - monthly Equinox (user_t), Verizon (user_v), mortgage, PG&E, Comcast
  - P2P with C, D, E, F, J, M, Q, S, X, Y
  - joint-account top-ups with B

Eight users (H, I, K, N, O, P, U, W) are deliberately NOT counterparties of
A — they exist in the bank but Alice has no transactional link to them. The
gate must BLOCK any claim about these users when A asks. This is the
"wall" the entitlement layer enforces.

Other heavy senders B/C/D have their own activity (some shared with A,
some with users A cannot see).

Idempotent: only inserts if the users table is empty.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argo.db import get_conn


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


# ----- Users --------------------------------------------------------------

# `kind` is Python-side only (not stored in the DB) — it drives transaction
# generation patterns. Establishments are merchants A interacts with.
USERS: list[dict] = [
    {"id": "user_a", "display_name": "Alice Chen",         "email": "alice@example.com",          "kind": "person"},
    {"id": "user_b", "display_name": "Bob Patel",          "email": "bob@example.com",            "kind": "person"},
    {"id": "user_c", "display_name": "Charlie Rivera",     "email": "charlie@example.com",        "kind": "person"},
    {"id": "user_d", "display_name": "Diana Kim",          "email": "diana@example.com",          "kind": "person"},
    {"id": "user_e", "display_name": "Ethan Brooks",       "email": "ethan@example.com",          "kind": "person"},
    {"id": "user_f", "display_name": "Fiona Garcia",       "email": "fiona@example.com",          "kind": "person"},
    {"id": "user_g", "display_name": "Starbucks Coffee",   "email": "treasury@starbucks.com",     "kind": "establishment"},
    {"id": "user_h", "display_name": "Hannah Lee",         "email": "hannah@example.com",         "kind": "person"},
    {"id": "user_i", "display_name": "Ivy Nguyen",         "email": "ivy@example.com",            "kind": "person"},
    {"id": "user_j", "display_name": "Joel Martinez",      "email": "joel@example.com",           "kind": "person"},
    {"id": "user_k", "display_name": "Kenji Tanaka",       "email": "kenji@example.com",          "kind": "person"},
    {"id": "user_l", "display_name": "Whole Foods Market", "email": "treasury@wholefoods.com",    "kind": "establishment"},
    {"id": "user_m", "display_name": "Mira Cohen",         "email": "mira@example.com",           "kind": "person"},
    {"id": "user_n", "display_name": "Naomi Singh",        "email": "naomi@example.com",          "kind": "person"},
    {"id": "user_o", "display_name": "Oliver Schmidt",     "email": "oliver@example.com",         "kind": "person"},
    {"id": "user_p", "display_name": "Priya Shah",         "email": "priya@example.com",          "kind": "person"},
    {"id": "user_q", "display_name": "Quincy Adams",       "email": "quincy@example.com",         "kind": "person"},
    {"id": "user_r", "display_name": "Shell Energy",       "email": "treasury@shellenergy.com",   "kind": "establishment"},
    {"id": "user_s", "display_name": "Sara Park",          "email": "sara@example.com",           "kind": "person"},
    {"id": "user_t", "display_name": "Equinox Fitness",    "email": "treasury@equinox.com",       "kind": "establishment"},
    {"id": "user_u", "display_name": "Uma Reddy",          "email": "uma@example.com",            "kind": "person"},
    {"id": "user_v", "display_name": "Verizon Wireless",   "email": "treasury@verizon.com",       "kind": "establishment"},
    {"id": "user_w", "display_name": "Wendy Chao",         "email": "wendy@example.com",          "kind": "person"},
    {"id": "user_x", "display_name": "Xander Liu",         "email": "xander@example.com",         "kind": "person"},
    {"id": "user_y", "display_name": "Yusuf Hassan",       "email": "yusuf@example.com",          "kind": "person"},
    {"id": "user_z", "display_name": "Acme Corp Payroll",  "email": "payroll@acmecorp.com",       "kind": "establishment"},
]

USERS_BY_ID = {u["id"]: u for u in USERS}


# ----- Accounts -----------------------------------------------------------

# Every person has a checking account. A & B share a joint. Establishments
# have no banking-side accounts — they appear only as counterparties on
# customer transactions.
ACCOUNTS: list[dict] = [
    {"id": "acct_a_chk",     "display_name": "Alice Checking",                "account_type": "individual", "last4": "4421", "balance_cents": 583450},
    {"id": "acct_b_chk",     "display_name": "Bob Checking",                  "account_type": "individual", "last4": "7782", "balance_cents": 1418000},
    {"id": "acct_c_chk",     "display_name": "Charlie Checking",              "account_type": "individual", "last4": "2014", "balance_cents": 224700},
    {"id": "acct_d_chk",     "display_name": "Diana Checking",                "account_type": "individual", "last4": "5503", "balance_cents": 612000},
    {"id": "acct_e_chk",     "display_name": "Ethan Checking",                "account_type": "individual", "last4": "8826", "balance_cents": 198400},
    {"id": "acct_f_chk",     "display_name": "Fiona Checking",                "account_type": "individual", "last4": "3304", "balance_cents": 305200},
    {"id": "acct_h_chk",     "display_name": "Hannah Checking",               "account_type": "individual", "last4": "9911", "balance_cents": 144800},
    {"id": "acct_i_chk",     "display_name": "Ivy Checking",                  "account_type": "individual", "last4": "6650", "balance_cents": 89300},
    {"id": "acct_j_chk",     "display_name": "Joel Checking",                 "account_type": "individual", "last4": "2278", "balance_cents": 256100},
    {"id": "acct_k_chk",     "display_name": "Kenji Checking",                "account_type": "individual", "last4": "4173", "balance_cents": 472900},
    {"id": "acct_m_chk",     "display_name": "Mira Checking",                 "account_type": "individual", "last4": "7755", "balance_cents": 188600},
    {"id": "acct_n_chk",     "display_name": "Naomi Checking",                "account_type": "individual", "last4": "1102", "balance_cents": 367200},
    {"id": "acct_o_chk",     "display_name": "Oliver Checking",               "account_type": "individual", "last4": "8409", "balance_cents": 215300},
    {"id": "acct_p_chk",     "display_name": "Priya Checking",                "account_type": "individual", "last4": "5598", "balance_cents": 412700},
    {"id": "acct_q_chk",     "display_name": "Quincy Checking",               "account_type": "individual", "last4": "3320", "balance_cents": 117400},
    {"id": "acct_s_chk",     "display_name": "Sara Checking",                 "account_type": "individual", "last4": "9067", "balance_cents": 278900},
    {"id": "acct_u_chk",     "display_name": "Uma Checking",                  "account_type": "individual", "last4": "4488", "balance_cents": 332100},
    {"id": "acct_w_chk",     "display_name": "Wendy Checking",                "account_type": "individual", "last4": "7714", "balance_cents": 156800},
    {"id": "acct_x_chk",     "display_name": "Xander Checking",               "account_type": "individual", "last4": "2231", "balance_cents": 198000},
    {"id": "acct_y_chk",     "display_name": "Yusuf Checking",                "account_type": "individual", "last4": "6045", "balance_cents": 245500},
    {"id": "acct_ab_joint",  "display_name": "Joint Checking (Alice & Bob)",  "account_type": "joint",      "last4": "9933", "balance_cents": 1182000},
]

OWNERSHIPS: list[tuple[str, str]] = [
    ("acct_a_chk", "user_a"),
    ("acct_b_chk", "user_b"),
    ("acct_c_chk", "user_c"),
    ("acct_d_chk", "user_d"),
    ("acct_e_chk", "user_e"),
    ("acct_f_chk", "user_f"),
    ("acct_h_chk", "user_h"),
    ("acct_i_chk", "user_i"),
    ("acct_j_chk", "user_j"),
    ("acct_k_chk", "user_k"),
    ("acct_m_chk", "user_m"),
    ("acct_n_chk", "user_n"),
    ("acct_o_chk", "user_o"),
    ("acct_p_chk", "user_p"),
    ("acct_q_chk", "user_q"),
    ("acct_s_chk", "user_s"),
    ("acct_u_chk", "user_u"),
    ("acct_w_chk", "user_w"),
    ("acct_x_chk", "user_x"),
    ("acct_y_chk", "user_y"),
    ("acct_ab_joint", "user_a"),
    ("acct_ab_joint", "user_b"),
]


# ----- Transaction generation --------------------------------------------

# Generated list is sorted by ts before insertion. Each tx_id assigned in
# order so consumers can rely on stable ids.

TxRow = tuple[str, int, str, str, str | None, str, str]
# (account_id, amount_cents, direction, cp_name, cp_user_id, memo, ts_iso)


def _weekly(start_iso: str, count: int, hour: int = 9, minute: int = 0) -> list[str]:
    base = datetime.fromisoformat(start_iso)
    return [
        (base + timedelta(weeks=i)).replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M:%S")
        for i in range(count)
    ]


def _biweekly(start_iso: str, count: int, hour: int = 9, minute: int = 0) -> list[str]:
    base = datetime.fromisoformat(start_iso)
    return [
        (base + timedelta(weeks=2 * i)).replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M:%S")
        for i in range(count)
    ]


def _monthly(months: list[tuple[int, int]], hour: int = 6, minute: int = 0) -> list[str]:
    return [
        f"2026-{m:02d}-{d:02d}T{hour:02d}:{minute:02d}:00"
        for (m, d) in months
    ]


def _build_transactions() -> list[TxRow]:
    rows: list[TxRow] = []

    def add(account: str, cents: int, direction: str, cp_name: str,
            cp_user: str | None, memo: str, ts: str) -> None:
        rows.append((account, cents, direction, cp_name, cp_user, memo, ts))

    def transfer(from_acct: str, from_user_id: str,
                 to_acct: str, to_user_id: str,
                 cents: int, memo: str, ts: str) -> None:
        """Internal transfer between two of the bank's accounts. Records both sides."""
        from_user = USERS_BY_ID[from_user_id]
        to_user = USERS_BY_ID[to_user_id]
        add(from_acct, cents, "outbound", to_user["display_name"], to_user_id, memo, ts)
        add(to_acct, cents, "inbound", from_user["display_name"], from_user_id, memo, ts)

    # ==== ALICE'S PAYCHECK FROM ACME (user_z) — biweekly Friday ====
    paycheck_dates = _biweekly("2026-02-06", 8, hour=9)
    paycheck_amounts = [320000, 320000, 325000, 325000, 325000, 325000, 325000, 325000]
    for date_iso, amt in zip(paycheck_dates, paycheck_amounts, strict=True):
        add("acct_a_chk", amt, "inbound", "Acme Corp Payroll", "user_z",
            f"Salary — {date_iso[:10]}", date_iso)

    # ==== ALICE: weekly Starbucks (user_g) ====
    sb_dates = _weekly("2026-02-03", 16, hour=8, minute=15)
    sb_amounts = [685, 720, 685, 750, 685, 685, 825, 685, 720, 685, 825, 685, 720, 685, 685, 720]
    for d, a in zip(sb_dates, sb_amounts, strict=True):
        add("acct_a_chk", a, "outbound", "Starbucks Coffee", "user_g",
            "Morning coffee + pastry", d)

    # ==== ALICE: weekly Whole Foods (user_l) ====
    wf_dates = _weekly("2026-02-07", 16, hour=18, minute=30)
    wf_amounts = [9240, 11580, 8650, 12340, 9870, 10550, 8430, 11220,
                  9990, 13410, 8780, 10290, 9650, 11860, 9120, 10670]
    for d, a in zip(wf_dates, wf_amounts, strict=True):
        add("acct_a_chk", a, "outbound", "Whole Foods Market", "user_l",
            "Groceries", d)

    # ==== ALICE: biweekly Shell (user_r) ====
    sh_dates = _biweekly("2026-02-05", 8, hour=17)
    sh_amounts = [4820, 5240, 4650, 5510, 4980, 5320, 4720, 5040]
    for d, a in zip(sh_dates, sh_amounts, strict=True):
        add("acct_a_chk", a, "outbound", "Shell Energy", "user_r",
            "Gas — fill up", d)

    # ==== ALICE: monthly Equinox (user_t) ====
    for d in _monthly([(2, 1), (3, 1), (4, 1), (5, 1)], hour=6):
        add("acct_a_chk", 16500, "outbound", "Equinox Fitness", "user_t",
            f"Gym membership — {d[:7]}", d)

    # ==== ALICE: monthly Verizon (user_v) ====
    for d in _monthly([(2, 12), (3, 12), (4, 12), (5, 12)], hour=10):
        add("acct_a_chk", 8500, "outbound", "Verizon Wireless", "user_v",
            f"Phone bill — {d[:7]}", d)

    # ==== ALICE: monthly Spotify (string vendor) ====
    for d in _monthly([(2, 5), (3, 5), (4, 5), (5, 5)], hour=8):
        add("acct_a_chk", 1099, "outbound", "Spotify", None, "Subscription", d)

    # ==== ALICE: monthly Netflix (string vendor) ====
    for d in _monthly([(2, 18), (3, 18), (4, 18), (5, 18)], hour=8):
        add("acct_a_chk", 1549, "outbound", "Netflix", None, "Subscription", d)

    # ==== ALICE ↔ JOINT: monthly top-ups ====
    joint_topup_dates = _monthly([(2, 28), (3, 28), (4, 28), (5, 15)], hour=18)
    for d in joint_topup_dates:
        transfer("acct_a_chk", "user_a", "acct_ab_joint", "user_a",
                 150000, "Top-up to joint", d)

    # ==== BOB ↔ JOINT: monthly top-ups ====
    for d in _monthly([(2, 28), (3, 28), (4, 28), (5, 15)], hour=18, minute=30):
        transfer("acct_b_chk", "user_b", "acct_ab_joint", "user_b",
                 200000, "Top-up to joint", d)

    # ==== JOINT outflows: mortgage / PG&E / Comcast (string vendors) ====
    for d in _monthly([(2, 1), (3, 1), (4, 1), (5, 1)], hour=6):
        add("acct_ab_joint", 280000, "outbound", "Wells Fargo Mortgage", None,
            f"Mortgage — {d[:7]}", d)
    for d in _monthly([(2, 3), (3, 3), (4, 3), (5, 3)], hour=10):
        add("acct_ab_joint", 21400, "outbound", "PG&E", None,
            f"Utilities — {d[:7]}", d)
    for d in _monthly([(2, 5), (3, 5), (4, 5), (5, 5)], hour=10):
        add("acct_ab_joint", 8995, "outbound", "Comcast", None,
            f"Internet — {d[:7]}", d)

    # ==== ALICE'S P2P TRANSACTIONS ====
    # A → C: Tahoe trip & hiking shares (mix of directions)
    transfer("acct_a_chk", "user_a", "acct_c_chk", "user_c",
             9000, "Hiking trip share", "2026-02-25T14:00:00")
    transfer("acct_c_chk", "user_c", "acct_a_chk", "user_a",
             30000, "Tahoe trip share", "2026-03-15T20:00:00")
    transfer("acct_a_chk", "user_a", "acct_c_chk", "user_c",
             4500, "Pizza split", "2026-04-08T20:30:00")
    transfer("acct_c_chk", "user_c", "acct_a_chk", "user_a",
             12500, "Concert tickets", "2026-04-22T19:00:00")
    transfer("acct_a_chk", "user_a", "acct_c_chk", "user_c",
             6800, "Dinner — Lupa", "2026-05-09T21:30:00")

    # A → D: Diana — birthday gift, vacation split
    transfer("acct_a_chk", "user_a", "acct_d_chk", "user_d",
             10000, "Birthday gift", "2026-02-22T11:00:00")
    transfer("acct_a_chk", "user_a", "acct_d_chk", "user_d",
             47500, "Tahoe cabin — Diana's share refund", "2026-03-20T16:00:00")
    transfer("acct_d_chk", "user_d", "acct_a_chk", "user_a",
             5200, "Brunch split", "2026-04-12T13:00:00")
    transfer("acct_a_chk", "user_a", "acct_d_chk", "user_d",
             3000, "Coffee + book", "2026-05-04T15:00:00")

    # A → E: Ethan — borrowed money repay
    transfer("acct_e_chk", "user_e", "acct_a_chk", "user_a",
             50000, "Repaying loan", "2026-02-19T12:00:00")
    transfer("acct_a_chk", "user_a", "acct_e_chk", "user_e",
             8500, "Wedding gift contribution", "2026-04-26T10:00:00")

    # A → F: Fiona — concert tickets
    transfer("acct_a_chk", "user_a", "acct_f_chk", "user_f",
             18000, "Beyoncé tickets — my half", "2026-03-08T22:00:00")
    transfer("acct_f_chk", "user_f", "acct_a_chk", "user_a",
             4200, "Uber split", "2026-04-15T23:30:00")

    # A → J: Joel — sports league + book club
    transfer("acct_a_chk", "user_a", "acct_j_chk", "user_j",
             7500, "Tennis league dues", "2026-02-14T10:00:00")
    transfer("acct_a_chk", "user_a", "acct_j_chk", "user_j",
             7500, "Tennis league dues — Q2", "2026-05-14T10:00:00")
    transfer("acct_j_chk", "user_j", "acct_a_chk", "user_a",
             2800, "Book club — book share", "2026-03-25T20:00:00")

    # A → M: Mira — vacation home split, brunches
    transfer("acct_a_chk", "user_a", "acct_m_chk", "user_m",
             65000, "Vacation home — my share Apr", "2026-04-02T09:00:00")
    transfer("acct_m_chk", "user_m", "acct_a_chk", "user_a",
             3200, "Brunch share", "2026-03-29T13:00:00")
    transfer("acct_a_chk", "user_a", "acct_m_chk", "user_m",
             4750, "Dinner @ Cosme", "2026-05-11T21:00:00")

    # A → Q: Quincy — repaid loan
    transfer("acct_q_chk", "user_q", "acct_a_chk", "user_a",
             80000, "Repaying loan from January", "2026-02-10T11:00:00")

    # A → S: Sara — wedding gift, then a brunch
    transfer("acct_a_chk", "user_a", "acct_s_chk", "user_s",
             25000, "Wedding gift — congrats!", "2026-03-12T10:00:00")
    transfer("acct_s_chk", "user_s", "acct_a_chk", "user_a",
             4500, "Brunch split", "2026-05-03T13:30:00")

    # A → X: Xander — sublet payment
    transfer("acct_a_chk", "user_a", "acct_x_chk", "user_x",
             60000, "Sublet — May", "2026-04-30T09:00:00")
    transfer("acct_a_chk", "user_a", "acct_x_chk", "user_x",
             5500, "Furniture share — IKEA run", "2026-05-08T17:00:00")

    # A → Y: Yusuf — sports league, occasional dinner
    transfer("acct_a_chk", "user_a", "acct_y_chk", "user_y",
             7500, "Soccer league dues", "2026-02-15T10:00:00")
    transfer("acct_y_chk", "user_y", "acct_a_chk", "user_a",
             6200, "Dinner reimbursement", "2026-04-18T21:00:00")

    # ==== ALICE: misc string-vendor outflows ====
    misc = [
        ("acct_a_chk", 6740, "outbound", "Chen's Tea House", None, "Tea + scones", "2026-02-18T15:00:00"),
        ("acct_a_chk", 4200, "outbound", "Rivera Studios", None, "Yoga class drop-in", "2026-03-04T18:00:00"),
        ("acct_a_chk", 12800, "outbound", "Uber", None, "Airport ride", "2026-03-22T05:00:00"),
        ("acct_a_chk", 8950, "outbound", "Lyft", None, "Late-night cab", "2026-04-06T01:30:00"),
        ("acct_a_chk", 23400, "outbound", "Delta Airlines", None, "Flight — SFO→JFK", "2026-04-10T12:00:00"),
        ("acct_a_chk", 17800, "outbound", "Marriott Hotels", None, "NYC trip — 1 night", "2026-04-11T22:00:00"),
        ("acct_a_chk", 4290, "outbound", "Trader Joe's", None, "Mid-week groceries", "2026-04-25T19:00:00"),
        ("acct_a_chk", 3650, "outbound", "Chipotle", None, "Lunch", "2026-05-06T13:00:00"),
        ("acct_a_chk", 2200, "outbound", "Blue Bottle Coffee", None, "Saturday coffee", "2026-05-10T10:00:00"),
        ("acct_a_chk", 14500, "outbound", "REI", None, "Hiking boots", "2026-05-17T15:00:00"),
        ("acct_a_chk", 6800, "outbound", "Trader Joe's", None, "Mid-week groceries", "2026-05-20T19:00:00"),
        ("acct_a_chk", 4800, "outbound", "Sweetgreen", None, "Salad bowl", "2026-05-22T13:00:00"),
    ]
    for row in misc:
        add(*row)  # type: ignore[misc]

    # ==== BACKGROUND ACTIVITY for B/C/D (some shared with A above) ====
    # B's paycheck + grocery + own P2P. Goes to/from people A does not see (H).
    for d in _biweekly("2026-02-13", 8, hour=9):
        add("acct_b_chk", 480000, "inbound", "Sierra Health Systems", None,
            f"Salary — {d[:10]}", d)
    transfer("acct_b_chk", "user_b", "acct_h_chk", "user_h",
             14000, "Trip share", "2026-03-09T20:00:00")
    transfer("acct_h_chk", "user_h", "acct_b_chk", "user_b",
             3200, "Lunch split", "2026-04-19T13:00:00")
    transfer("acct_b_chk", "user_b", "acct_c_chk", "user_c",
             20000, "Logo design commission", "2026-03-05T11:00:00")

    # C's freelance income (string vendor) + relationships with I (not visible to A)
    for d in _biweekly("2026-02-04", 8, hour=9):
        add("acct_c_chk", 240000, "inbound", "Rivera Design Co", None,
            f"Client payment — {d[:10]}", d)
    transfer("acct_c_chk", "user_c", "acct_i_chk", "user_i",
             15000, "Studio rent — March", "2026-03-01T09:00:00")
    transfer("acct_i_chk", "user_i", "acct_c_chk", "user_c",
             8000, "Design commission split", "2026-04-22T15:00:00")

    # D's paycheck + relationship with K (not visible to A)
    for d in _biweekly("2026-02-13", 8, hour=9):
        add("acct_d_chk", 380000, "inbound", "Lyra Pharma", None,
            f"Salary — {d[:10]}", d)
    transfer("acct_d_chk", "user_d", "acct_k_chk", "user_k",
             22000, "Roadtrip share", "2026-04-05T18:00:00")
    transfer("acct_k_chk", "user_k", "acct_d_chk", "user_d",
             6700, "Concert tickets", "2026-05-02T20:00:00")

    # ==== BACKGROUND for E/F/J/M/Q/S/X/Y (people A interacts with) ====
    # Each gets a paycheck-like inflow + an unrelated outflow.
    add("acct_e_chk", 290000, "inbound", "Globex Corp", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_e_chk", 290000, "inbound", "Globex Corp", None, "Salary — May 1", "2026-05-01T09:00:00")
    add("acct_e_chk", 18500, "outbound", "Costco", None, "Costco run", "2026-04-08T16:00:00")

    add("acct_f_chk", 310000, "inbound", "Initech LLC", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_f_chk", 4800, "outbound", "Trader Joe's", None, "Groceries", "2026-04-19T18:00:00")

    add("acct_j_chk", 265000, "inbound", "Hooli", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_j_chk", 13200, "outbound", "Amazon", None, "Home goods", "2026-04-14T11:00:00")

    add("acct_m_chk", 340000, "inbound", "Vandelay Industries", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_m_chk", 9200, "outbound", "Whole Foods Market", "user_l", "Groceries", "2026-04-12T19:00:00")

    add("acct_q_chk", 240000, "inbound", "Pied Piper Inc", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_q_chk", 6500, "outbound", "Shell Energy", "user_r", "Gas", "2026-04-22T17:00:00")

    add("acct_s_chk", 285000, "inbound", "Soylent Corp", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_s_chk", 12000, "outbound", "Starbucks Coffee", "user_g", "Office coffee run", "2026-04-30T10:00:00")

    add("acct_x_chk", 280000, "inbound", "Massive Dynamic", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_x_chk", 4200, "outbound", "Equinox Fitness", "user_t", "Drop-in class", "2026-04-22T07:00:00")

    add("acct_y_chk", 260000, "inbound", "Cyberdyne Systems", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_y_chk", 7600, "outbound", "Whole Foods Market", "user_l", "Groceries", "2026-05-02T18:00:00")

    # ==== BACKGROUND for H/I/K/N/O/P/U/W (NOT visible to A) ====
    # These users exist and have activity, but A has zero transactional link
    # to them — the gate must BLOCK any claim that names them.
    add("acct_h_chk", 270000, "inbound", "Initech LLC", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_h_chk", 8400, "outbound", "Whole Foods Market", "user_l", "Groceries", "2026-04-09T18:00:00")

    add("acct_i_chk", 195000, "inbound", "Rivera Design Co", None, "Contract — Mar 20", "2026-03-20T09:00:00")
    add("acct_i_chk", 5200, "outbound", "Starbucks Coffee", "user_g", "Coffee", "2026-04-15T08:00:00")

    add("acct_k_chk", 350000, "inbound", "Stark Industries", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_k_chk", 9800, "outbound", "Verizon Wireless", "user_v", "Phone bill", "2026-04-12T10:00:00")

    add("acct_n_chk", 240000, "inbound", "Wayne Enterprises", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_n_chk", 4800, "outbound", "Equinox Fitness", "user_t", "Drop-in", "2026-04-22T07:00:00")
    transfer("acct_n_chk", "user_n", "acct_o_chk", "user_o",
             12500, "Vacation share", "2026-04-25T15:00:00")

    add("acct_o_chk", 280000, "inbound", "Oscorp", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_o_chk", 6200, "outbound", "Shell Energy", "user_r", "Gas", "2026-05-08T17:00:00")

    add("acct_p_chk", 320000, "inbound", "Tyrell Corp", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_p_chk", 10200, "outbound", "Whole Foods Market", "user_l", "Groceries", "2026-04-18T18:00:00")

    add("acct_u_chk", 260000, "inbound", "Umbrella Corp", None, "Salary — Mar 6", "2026-03-06T09:00:00")
    add("acct_u_chk", 5800, "outbound", "Starbucks Coffee", "user_g", "Weekly coffee", "2026-04-29T08:00:00")
    transfer("acct_u_chk", "user_u", "acct_w_chk", "user_w",
             18000, "Concert split", "2026-05-06T20:00:00")

    add("acct_w_chk", 220000, "inbound", "Massive Dynamic", None, "Salary — Mar 13", "2026-03-13T09:00:00")
    add("acct_w_chk", 4500, "outbound", "Verizon Wireless", "user_v", "Phone bill", "2026-05-12T10:00:00")

    return rows


_TX_DATA = sorted(_build_transactions(), key=lambda r: (r[6], r[0]))


# ----- Entitlement helpers (used by entitlements.py) ---------------------

def counterparties_for(user_id: str) -> set[str]:
    """user_ids the asking user has a transactional link to.

    Two paths qualify: (a) any tx on an account they own naming the other
    user as counterparty_user_id, and (b) anyone else with ownership of a
    joint account they're on.
    """
    owned_accts = {acct for (acct, uid) in OWNERSHIPS if uid == user_id}
    cps: set[str] = set()

    for (acct, _cents, _dir, _cp_name, cp_user, _memo, _ts) in _TX_DATA:
        if acct in owned_accts and cp_user is not None and cp_user != user_id:
            cps.add(cp_user)

    for (acct, uid) in OWNERSHIPS:
        if acct in owned_accts and uid != user_id:
            cps.add(uid)

    return cps


def accounts_for(user_id: str) -> set[str]:
    return {acct for (acct, uid) in OWNERSHIPS if uid == user_id}


# ----- Insert into Postgres ---------------------------------------------

def seed_if_empty() -> None:
    """Idempotent seed: short-circuits when the users table is non-empty."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        row = cur.fetchone()
        if row and row["n"] > 0:
            return

        cur.executemany(
            "INSERT INTO users (id, display_name, email) VALUES (%s, %s, %s)",
            [(u["id"], u["display_name"], u["email"]) for u in USERS],
        )
        cur.executemany(
            "INSERT INTO accounts (id, display_name, account_type, last4, balance_cents) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(a["id"], a["display_name"], a["account_type"], a["last4"], a["balance_cents"])
             for a in ACCOUNTS],
        )
        cur.executemany(
            "INSERT INTO account_owners (account_id, user_id) VALUES (%s, %s)",
            OWNERSHIPS,
        )
        cur.executemany(
            "INSERT INTO transactions (id, account_id, amount_cents, direction, "
            "counterparty_name, counterparty_user_id, memo, ts) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (f"tx_{i:04d}", acct, cents, direction, cp_name, cp_user, memo, _ts(ts))
                for i, (acct, cents, direction, cp_name, cp_user, memo, ts)
                in enumerate(_TX_DATA, start=1)
            ],
        )
        conn.commit()
