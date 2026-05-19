"""Naive chat path: stuff ALL user data into the system prompt and let the LLM answer.

This is the "before Argo" baseline used in the demo's split-screen. By design it
exposes everyone's data to the model so the LLM can — and will — leak. The
entitlement-aware path replaces this in W3.
"""

from argo.config import settings
from argo.db.queries import get_all_accounts_with_owners, get_all_transactions, get_user
from argo.llm import call_chat_model


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _render_accounts(accounts: list[dict]) -> str:
    lines = ["=== ALL ACCOUNTS ==="]
    for a in accounts:
        owners = ", ".join(a["owner_names"])
        lines.append(
            f"- id={a['id']} | {a['display_name']} (••••{a['last4']}, "
            f"{a['account_type']}) | balance {_money(a['balance_cents'])} "
            f"| owners: {owners}"
        )
    return "\n".join(lines)


def _render_transactions(txs: list[dict]) -> str:
    lines = ["=== ALL TRANSACTIONS (chronological) ==="]
    for t in txs:
        date = t["ts"].strftime("%Y-%m-%d")
        cp_name = t["counterparty_name"] or "(unspecified)"
        cp_user = t["counterparty_user_id"] or "-"
        memo = t["memo"] or "—"
        lines.append(
            f"[{date}] account={t['account_id']} ({t['account_display']}) | "
            f"{t['direction']} {_money(t['amount_cents'])} | "
            f"counterparty: {cp_name} (user_id={cp_user}) | memo: {memo}"
        )
    return "\n".join(lines)


SYSTEM_TEMPLATE = """You are a customer-service assistant for Argo Bank, helping customer {user_name} (id: {user_id}).

You have access to Argo Bank's full customer dataset below. Use it to answer the customer's questions naturally and helpfully. Provide specific numbers, dates, and names when they help the customer.

{accounts_block}

{transactions_block}
"""


def build_naive_system_prompt(user_id: str) -> str:
    user = get_user(user_id)
    if user is None:
        raise ValueError(f"unknown user_id: {user_id}")
    accounts = get_all_accounts_with_owners()
    txs = get_all_transactions()
    return SYSTEM_TEMPLATE.format(
        user_name=user["display_name"],
        user_id=user_id,
        accounts_block=_render_accounts(accounts),
        transactions_block=_render_transactions(txs),
    )


def naive_chat(user_id: str, query: str) -> tuple[str, str, int]:
    """Returns (response_text, model_id, latency_ms)."""
    system = build_naive_system_prompt(user_id)
    response, latency = call_chat_model(system, query)
    return response, settings.chat_model, latency
