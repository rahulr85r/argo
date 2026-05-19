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
