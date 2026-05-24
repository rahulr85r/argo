"""Policy loader for Argo's GRC-reviewable banking.toml.

The TOML file declares the field whitelist and the counterparty-graph
rules. This module parses it into typed dataclasses at import time and
exposes a single `POLICY` singleton. Rule *evaluation* lives in the
entitlement adapters (per-adapter, because the data source differs
between production Postgres and the in-memory seed); this module only
describes the rules.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from argo.claims import ClaimType


_VALID_CLAIM_TYPES = frozenset(get_args(ClaimType))
_VALID_RULE_TYPES = frozenset({"recent_payment", "joint_account_co_owner"})


@dataclass(frozen=True)
class CounterpartyRule:
    type: str
    lookback_days: int | None = None


@dataclass(frozen=True)
class Policy:
    counterparty_fields: frozenset[ClaimType]
    counterparty_rules: tuple[CounterpartyRule, ...]


_POLICY_PATH = Path(__file__).parent / "banking.toml"


def load_policy(path: Path = _POLICY_PATH) -> Policy:
    raw = tomllib.loads(path.read_text())

    fields_raw = raw.get("counterparty_fields", [])
    unknown_fields = set(fields_raw) - _VALID_CLAIM_TYPES
    if unknown_fields:
        raise ValueError(
            f"policy {path.name}: unknown claim types in counterparty_fields: "
            f"{sorted(unknown_fields)}"
        )

    rules_raw = raw.get("counterparty_rules", [])
    rules: list[CounterpartyRule] = []
    for i, r in enumerate(rules_raw):
        rule_type = r.get("type")
        if rule_type not in _VALID_RULE_TYPES:
            raise ValueError(
                f"policy {path.name}: unknown rule type {rule_type!r} at index {i}; "
                f"expected one of {sorted(_VALID_RULE_TYPES)}"
            )
        if rule_type == "recent_payment" and r.get("lookback_days") is None:
            raise ValueError(
                f"policy {path.name}: recent_payment rule at index {i} "
                f"is missing required field 'lookback_days'"
            )
        rules.append(CounterpartyRule(
            type=rule_type,
            lookback_days=r.get("lookback_days"),
        ))

    return Policy(
        counterparty_fields=frozenset(fields_raw),
        counterparty_rules=tuple(rules),
    )


POLICY: Policy = load_policy()


__all__ = ["POLICY", "Policy", "CounterpartyRule", "load_policy"]
