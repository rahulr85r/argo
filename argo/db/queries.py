"""Read-side helpers for the demo domain."""

from argo.db import get_conn


def get_user(user_id: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, display_name, email FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()


def get_all_users() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, display_name, email FROM users ORDER BY id")
        return cur.fetchall()


def get_all_accounts_with_owners() -> list[dict]:
    """Returns each account joined with the list of its owners."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.display_name, a.account_type, a.last4, a.balance_cents,
                   array_agg(u.display_name ORDER BY u.id) AS owner_names,
                   array_agg(u.id ORDER BY u.id) AS owner_ids
            FROM accounts a
            JOIN account_owners ao ON ao.account_id = a.id
            JOIN users u ON u.id = ao.user_id
            GROUP BY a.id, a.display_name, a.account_type, a.last4, a.balance_cents
            ORDER BY a.id
            """
        )
        return cur.fetchall()


def get_all_transactions() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.account_id, a.display_name AS account_display,
                   t.amount_cents, t.direction, t.counterparty_name,
                   t.counterparty_user_id, t.memo, t.ts
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            ORDER BY t.ts ASC, t.id ASC
            """
        )
        return cur.fetchall()


def get_account_ids_for_user(user_id: str) -> list[str]:
    """Return account_ids the user owns (individual + every joint they are on)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT account_id FROM account_owners WHERE user_id = %s",
            (user_id,),
        )
        return [r["account_id"] for r in cur.fetchall()]


def get_counterparty_user_ids_for_user(user_id: str) -> list[str]:
    """Distinct user_ids the asking user has a transactional link to.

    Two paths qualify:
      (a) any tx on an account the user owns naming the other party as
          counterparty_user_id,
      (b) anyone else who co-owns an account the user is on (joint-account
          co-owners — even before they exchange any tx, the co-ownership
          itself is a visibility-creating relationship).
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT cp_user FROM (
                SELECT t.counterparty_user_id AS cp_user
                FROM transactions t
                JOIN account_owners ao ON ao.account_id = t.account_id
                WHERE ao.user_id = %(uid)s
                  AND t.counterparty_user_id IS NOT NULL
                  AND t.counterparty_user_id <> %(uid)s
                UNION
                SELECT other.user_id AS cp_user
                FROM account_owners mine
                JOIN account_owners other USING (account_id)
                WHERE mine.user_id = %(uid)s
                  AND other.user_id <> %(uid)s
            ) cps
            """,
            {"uid": user_id},
        )
        return [r["cp_user"] for r in cur.fetchall()]


def get_user_transactions(user_id: str) -> list[dict]:
    """Transactions on every account the user owns (individual + joint).

    Used by the source-span verifier to confirm a transaction claim has any
    origin in the asking user's actually-visible data.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.account_id, a.display_name AS account_display,
                   t.amount_cents, t.direction, t.counterparty_name,
                   t.counterparty_user_id, t.memo, t.ts
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN account_owners ao ON ao.account_id = t.account_id
            WHERE ao.user_id = %s
            ORDER BY t.ts ASC, t.id ASC
            """,
            (user_id,),
        )
        return cur.fetchall()
