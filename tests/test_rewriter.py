"""Tests for argo.rewriter.

Pure-Python — no LLM calls. Verifier integration is exercised in
scripts/run_verifier_smoke.py.
"""

import json
from pathlib import Path

import pytest

from argo.claims import Claim
from argo.entitlements import (
    ClaimVerdict,
    SeedDerivedAdapter,
    VerdictResult,
    check_claim,
)
from argo.rewriter import REDACTION_MARKER, REFUSAL_TEXT, rewrite_response

LABELED = {
    ex["id"]: ex
    for ex in json.loads(
        (Path(__file__).resolve().parent.parent / "eval" / "labeled_claims.json").read_text()
    )
}


def _claims_for(ex_id: str) -> list[Claim]:
    return [Claim.model_validate(c) for c in LABELED[ex_id]["expected_claims"]]


def _entitlement_verdicts(ex_id: str) -> list[tuple[Claim, VerdictResult]]:
    """Run only the entitlement layer — leaves NEEDS_SOURCE_CHECK intact."""
    ex = LABELED[ex_id]
    bundle = SeedDerivedAdapter().get_bundle(ex["user_id"])
    claims = _claims_for(ex_id)
    return [(c, check_claim(c, bundle)) for c in claims]


# ----- core redaction behavior ------------------------------------------


def test_all_allow_response_unchanged():
    """b1: every claim ALLOW → response text untouched."""
    cvs = _entitlement_verdicts("b1_q1_own_checking")
    assert all(v.verdict == ClaimVerdict.ALLOW for _, v in cvs)
    result = rewrite_response(LABELED["b1_q1_own_checking"]["response"], cvs)
    assert result.final_response == LABELED["b1_q1_own_checking"]["response"]
    assert not result.whole_blocked
    assert result.redacted_chars == 0


def test_q4_charlie_balance_whole_blocks():
    """b4: every claim BLOCK + spans cover most of response → whole-block refusal."""
    cvs = _entitlement_verdicts("b4_q4_charlie_balance")
    result = rewrite_response(LABELED["b4_q4_charlie_balance"]["response"], cvs)
    assert result.whole_blocked
    assert result.final_response == REFUSAL_TEXT


def test_q6_bob_balance_whole_blocks():
    """b6: parallel to b4 — different subject, same outcome."""
    cvs = _entitlement_verdicts("b6_q6_bob_balance")
    result = rewrite_response(LABELED["b6_q6_bob_balance"]["response"], cvs)
    assert result.whole_blocked
    assert result.final_response == REFUSAL_TEXT


def test_e13_mixed_balances_partial_redaction():
    """e13: 'Alice Checking $X, Bob Checking $Y, Charlie Checking $Z' — A's claim
    allowed; B's and C's redacted. Below threshold → response stays partial."""
    ex = LABELED["e13_other_users_balance_in_one_sentence"]
    cvs = _entitlement_verdicts("e13_other_users_balance_in_one_sentence")
    result = rewrite_response(ex["response"], cvs)
    assert not result.whole_blocked
    assert "Alice Checking is at $4,250.00" in result.final_response
    assert REDACTION_MARKER in result.final_response
    assert "$12,890.00" not in result.final_response
    assert "$1,375.00" not in result.final_response


def test_empty_response_passes_through():
    """Empty response → empty output, no LLM action."""
    result = rewrite_response("", [])
    assert result.final_response == ""
    assert not result.whole_blocked


def test_hallucinated_subject_redacts():
    """e10: subject='unknown' → BLOCK at entitlement layer → REDACTION_MARKER in output."""
    ex = LABELED["e10_hallucinated_party"]
    cvs = _entitlement_verdicts("e10_hallucinated_party")
    result = rewrite_response(ex["response"], cvs)
    # e10's single claim's span covers the whole response → whole-block triggers
    assert result.whole_blocked
    assert result.final_response == REFUSAL_TEXT


# ----- threshold behavior -----------------------------------------------


def test_threshold_exact_boundary():
    """Just-under threshold stays partial; just-over flips to whole-block."""
    cvs = _entitlement_verdicts("e13_other_users_balance_in_one_sentence")
    text = LABELED["e13_other_users_balance_in_one_sentence"]["response"]
    # at threshold=0.01 every example flips to whole-block
    low = rewrite_response(text, cvs, block_threshold=0.01)
    assert low.whole_blocked
    # at threshold=0.99 only an effectively all-redacted response would block
    high = rewrite_response(text, cvs, block_threshold=0.99)
    assert not high.whole_blocked


# ----- span dedup / subsumption -----------------------------------------


def test_subsumed_spans_collapse():
    """When one span fully contains another, only the larger is replaced."""
    text = "Alice Chen has account ending 4421 with balance $4,250.00."
    span_outer = "Alice Chen has account ending 4421 with balance $4,250.00"
    span_inner = "ending 4421"

    # Synthesize two claims pointing at the same content, both BLOCK
    c_outer = Claim(
        text="outer", subject="user_a", type="other",
        role="primary", source_span=span_outer,
    )
    c_inner = Claim(
        text="inner", subject="user_a", type="other",
        role="primary", source_span=span_inner,
    )
    v = VerdictResult(ClaimVerdict.BLOCK, "test")

    result = rewrite_response(text, [(c_outer, v), (c_inner, v)], block_threshold=0.99)
    # Only one [redacted] marker — the larger span subsumed the smaller
    assert result.final_response.count(REDACTION_MARKER) == 1
    # Inner span doesn't appear separately (subsumed by outer)
    assert "ending 4421" not in result.final_response


# ----- audit trail ------------------------------------------------------


def test_audits_preserve_every_claim_with_reason():
    """Even on whole-block, the audit list keeps every claim + verdict + reason."""
    cvs = _entitlement_verdicts("b4_q4_charlie_balance")
    result = rewrite_response(LABELED["b4_q4_charlie_balance"]["response"], cvs)
    assert len(result.audits) == 3
    assert all(a.verdict == ClaimVerdict.BLOCK for a in result.audits)
    assert all(a.reason for a in result.audits)


def test_needs_source_check_fails_closed():
    """If NEEDS_SOURCE_CHECK reaches the rewriter (pipeline bug), treat as REDACT."""
    c = Claim(
        text="t", subject="user_c", type="transaction",
        role="counterparty", source_span="some span",
    )
    text = "leading text some span trailing text"
    v = VerdictResult(ClaimVerdict.NEEDS_SOURCE_CHECK, "leaked through")
    result = rewrite_response(text, [(c, v)], block_threshold=0.99)
    assert REDACTION_MARKER in result.final_response
    assert "some span" not in result.final_response


# ----- pipeline-shape sanity --------------------------------------------


@pytest.mark.parametrize("ex_id,expect_whole_block", [
    ("b1_q1_own_checking", False),    # all ALLOW
    ("b2_q2_joint_balance", False),   # all ALLOW
    ("b4_q4_charlie_balance", True),  # all BLOCK
    ("b6_q6_bob_balance", True),      # all BLOCK
    ("b7_q7_user_b_joint", False),    # all ALLOW
])
def test_demo_pivot_shapes(ex_id, expect_whole_block):
    cvs = _entitlement_verdicts(ex_id)
    result = rewrite_response(LABELED[ex_id]["response"], cvs)
    assert result.whole_blocked == expect_whole_block
