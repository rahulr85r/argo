"""Test the Phase-0 entitlement policy against every labeled claim.

The EXPECTED_VERDICTS table declares, for each example in
eval/labeled_claims.json, what verdict each claim should receive in order.
Verdicts are abbreviated: A=ALLOW, B=BLOCK, S=NEEDS_SOURCE_CHECK.

When the policy changes, update this table — failures here are the audit
trail for entitlement-policy decisions.
"""

import json
from pathlib import Path

import pytest

from argo.claims import Claim
from argo.entitlements import (
    ClaimVerdict,
    EntitlementBundle,
    HardcodedAdapter,
    UnknownUserError,
    check_claim,
)


LABELED = json.loads(
    (Path(__file__).resolve().parent.parent / "eval" / "labeled_claims.json").read_text()
)
LABELED_BY_ID = {ex["id"]: ex for ex in LABELED}


# Verdict shorthand → enum
A = ClaimVerdict.ALLOW
B = ClaimVerdict.BLOCK
S = ClaimVerdict.NEEDS_SOURCE_CHECK


# Expected verdicts, parallel to expected_claims in eval/labeled_claims.json.
EXPECTED_VERDICTS: dict[str, list[ClaimVerdict]] = {
    # ===== Baselines =====
    "b1_q1_own_checking": [A, A, A, A, A, A, A, A],
    "b2_q2_joint_balance": [A, A, A, A],
    "b3_q3_money_sent_lastweek": [S, S, S, S],
    "b4_q4_charlie_balance": [B, B, B],
    "b5_q5_charlie_email": [B, B, A],
    "b6_q6_bob_balance": [B, B, B],
    "b7_q7_user_b_joint": [A, A, A, A],
    # ===== Edges =====
    "e01_clean_refusal": [],
    "e02_empty_response": [],
    "e03_pure_greeting": [A],
    "e04_confusion_vendor_rivera_studios": [S],
    "e05_confusion_vendor_chens_tea": [S],
    "e06_aggregate_own_account": [A],
    "e07_aggregate_cross_user": [A, A, A],
    "e08_counterparty_field_offlist_address": [B],
    "e09_counterparty_allowed_minimal": [S],
    "e10_hallucinated_party": [B],
    "e11_inbound_from_counterparty": [S],
    "e12_account_membership_third_party": [B, B, B],
    "e13_other_users_balance_in_one_sentence": [A, B, B],
    "e14_user_id_disclosure": [B, B, B, B],
    "e15_partial_refusal_with_subleak": [B],
    "e16_external_vendor_only_own_data": [S],
    "e17_email_pii_third_party": [B],
    "e18_joint_only_tx_other_owner": [S, S, S, S, S, S],
    "e19_aliased_name_inbound": [S],
}


def _params():
    """Yields (example_id, claim_index, claim, asking_user_id, expected_verdict)."""
    for ex_id, expected in EXPECTED_VERDICTS.items():
        ex = LABELED_BY_ID[ex_id]
        assert len(ex["expected_claims"]) == len(expected), (
            f"{ex_id}: {len(ex['expected_claims'])} labeled claims vs "
            f"{len(expected)} expected verdicts — table out of sync"
        )
        for i, (c, v) in enumerate(zip(ex["expected_claims"], expected, strict=True)):
            yield pytest.param(
                Claim.model_validate(c),
                ex["user_id"],
                v,
                id=f"{ex_id}#{i}",
            )


@pytest.mark.parametrize("claim,asking_user_id,expected", _params())
def test_verdict(claim: Claim, asking_user_id: str, expected: ClaimVerdict) -> None:
    bundle = HardcodedAdapter().get_bundle(asking_user_id)
    result = check_claim(claim, bundle)
    assert result.verdict == expected, (
        f"expected {expected} got {result.verdict}\n"
        f"  claim:  ({claim.subject}, {claim.type}, {claim.role})\n"
        f"  reason: {result.reason}"
    )


# ----- coverage sanity ---------------------------------------------------


def test_all_labeled_examples_have_expected_verdicts() -> None:
    """If labeled_claims.json grows, EXPECTED_VERDICTS must grow with it."""
    missing = {ex["id"] for ex in LABELED} - set(EXPECTED_VERDICTS)
    assert not missing, f"missing expected_verdicts for: {sorted(missing)}"


def test_known_users_resolve() -> None:
    adapter = HardcodedAdapter()
    for uid in ("user_a", "user_b", "user_c"):
        bundle = adapter.get_bundle(uid)
        assert bundle.user_id == uid
        assert uid in bundle.owned_subjects
        # Sanity: everyone has the same counterparty whitelist size
        assert len(bundle.counterparty_fields) == 3


def test_unknown_user_raises() -> None:
    with pytest.raises(UnknownUserError):
        HardcodedAdapter().get_bundle("user_does_not_exist")


def test_bundle_is_immutable() -> None:
    """EntitlementBundle uses frozenset + frozen dataclass so adapter responses
    can be cached/shared without policy mutation risks."""
    bundle = HardcodedAdapter().get_bundle("user_a")
    with pytest.raises(AttributeError):
        bundle.user_id = "user_z"  # type: ignore[misc]
    assert isinstance(bundle.owned_subjects, frozenset)
    assert isinstance(bundle.counterparty_visible, frozenset)


# ----- aggregate stats for the audit panel ------------------------------


def test_verdict_distribution_matches_demo_design() -> None:
    """Sanity-check the demo's storytelling: Q1/Q2/Q7 all ALLOW, Q4/Q6 all
    BLOCK, Q3 routes to source-check (verifier separates real from fake)."""
    def verdicts_for(ex_id: str) -> list[ClaimVerdict]:
        ex = LABELED_BY_ID[ex_id]
        bundle = HardcodedAdapter().get_bundle(ex["user_id"])
        return [
            check_claim(Claim.model_validate(c), bundle).verdict
            for c in ex["expected_claims"]
        ]

    # Allow-all queries
    for ex_id in ("b1_q1_own_checking", "b2_q2_joint_balance", "b7_q7_user_b_joint"):
        assert all(v == ClaimVerdict.ALLOW for v in verdicts_for(ex_id)), ex_id

    # Block-all queries
    for ex_id in ("b4_q4_charlie_balance", "b6_q6_bob_balance"):
        assert all(v == ClaimVerdict.BLOCK for v in verdicts_for(ex_id)), ex_id

    # Q3 — every tx claim defers to source-check
    assert all(v == ClaimVerdict.NEEDS_SOURCE_CHECK
               for v in verdicts_for("b3_q3_money_sent_lastweek"))
