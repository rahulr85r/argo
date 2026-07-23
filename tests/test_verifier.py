"""Tests for argo.verifier — verdict resolution, prompt construction, tx scoping.

Scope note: the verifier delegates the *match judgement* to a model, so these
tests pin everything around that judgement rather than the judgement itself —
that a `match: false` becomes REDACT, that non-deferred verdicts pass through
untouched, that the batch stays aligned with its claim list, and that the rows
the model needs to decide correctly actually reach the prompt. Whether Haiku
gets direction-reversal right on real data is measured by
`scripts/run_verifier_smoke.py` against the live model.

The scoping test matters most for the security story: the verifier must only
ever see the asking user's own transactions.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from argo.claims import Claim
from argo.db.seed import SeedTransactionSource
from argo.entitlements import ClaimVerdict, VerdictResult
from argo.llm import FakeLlmExhausted
from argo.verifier import LlmVerifier, verify_transaction_claims


def _claim(text: str = "Alice sent Charlie $45.00 on May 12", subject: str = "user_c") -> Claim:
    return Claim(
        text=text, subject=subject, type="transaction",
        role="counterparty", source_span=text,
    )


def _verifier() -> LlmVerifier:
    v = LlmVerifier()
    v._source = SeedTransactionSource()
    return v


def _match(tx_id: str = "tx_0042", reason: str = "ok") -> str:
    """One-result verifier payload: the model found a supporting row."""
    return f'{{"results": [{{"match": true, "matched_tx_id": "{tx_id}", "reason": "{reason}"}}]}}'


def _no_match(reason: str = "no row") -> str:
    """One-result verifier payload: the model found nothing to support the claim."""
    return f'{{"results": [{{"match": false, "matched_tx_id": null, "reason": "{reason}"}}]}}'


NEEDS = VerdictResult(ClaimVerdict.NEEDS_SOURCE_CHECK, "deferred")
ALLOWED = VerdictResult(ClaimVerdict.ALLOW, "owned subject")
BLOCKED = VerdictResult(ClaimVerdict.BLOCK, "not a counterparty")


# ----- match / no-match → terminal verdicts ------------------------------


def test_match_resolves_to_allow(fake_llm):
    fake_llm(judge='{"results": [{"match": true, "matched_tx_id": "tx_0042",'
                   ' "reason": "direction/amount/date/counterparty match"}]}')
    out = _verifier().resolve([(_claim(), NEEDS)], "user_a")

    verdict = out[0][1]
    assert verdict.verdict == ClaimVerdict.ALLOW
    assert "tx_0042" in verdict.reason  # cited row lands in the audit trail


def test_no_match_resolves_to_redact(fake_llm):
    """The hallucination catch: no supporting row → REDACT, with the reason kept."""
    fake_llm(judge='{"results": [{"match": false, "matched_tx_id": null,'
                   ' "reason": "direction reversed: claim asserts outbound, row is inbound"}]}')
    out = _verifier().resolve([(_claim(), NEEDS)], "user_a")

    verdict = out[0][1]
    assert verdict.verdict == ClaimVerdict.REDACT
    assert "direction reversed" in verdict.reason


def test_missing_match_key_is_treated_as_no_match(fake_llm):
    """Fail closed: a malformed per-claim result must not read as ALLOW."""
    fake_llm(judge='{"results": [{"matched_tx_id": null, "reason": "unsure"}]}')
    out = _verifier().resolve([(_claim(), NEEDS)], "user_a")
    assert out[0][1].verdict == ClaimVerdict.REDACT


# ----- only NEEDS_SOURCE_CHECK is touched --------------------------------


def test_terminal_verdicts_pass_through_untouched(fake_llm):
    client = fake_llm(judge=_match())
    claims = [(_claim("a"), ALLOWED), (_claim("b"), NEEDS), (_claim("c"), BLOCKED)]

    out = _verifier().resolve(claims, "user_a")

    assert out[0][1] is ALLOWED          # same object — untouched
    assert out[2][1] is BLOCKED
    assert out[1][1].verdict == ClaimVerdict.ALLOW
    # Exactly one round-trip, carrying exactly the one deferred claim.
    assert len(client.calls_of("judge")) == 1
    assert "b" in client.calls_of("judge")[0].user


def test_verdict_order_is_preserved_across_a_batch(fake_llm):
    """Results are positional — a shuffled mapping would attach the wrong
    verdict to the wrong span, which the rewriter would then redact blindly."""
    fake_llm(judge='{"results": ['
                   '{"match": false, "matched_tx_id": null, "reason": "no row"},'
                   '{"match": true, "matched_tx_id": "tx_0002", "reason": "ok"},'
                   '{"match": false, "matched_tx_id": null, "reason": "amount off"}]}')
    claims = [(_claim("first"), NEEDS), (_claim("second"), NEEDS), (_claim("third"), NEEDS)]

    out = _verifier().resolve(claims, "user_a")

    assert [v.verdict for _, v in out] == [
        ClaimVerdict.REDACT, ClaimVerdict.ALLOW, ClaimVerdict.REDACT,
    ]
    assert [c.text for c, _ in out] == ["first", "second", "third"]


def test_no_deferred_claims_skips_the_llm(fake_llm):
    """The documented optimisation: all-terminal input costs zero round-trips."""
    client = fake_llm(judge='{"results": []}')
    claims = [(_claim("a"), ALLOWED), (_claim("b"), BLOCKED)]

    out = _verifier().resolve(claims, "user_a")

    assert out == claims
    assert client.calls == []


def test_empty_claim_list_makes_no_call(fake_llm):
    client = fake_llm()
    assert verify_transaction_claims([], "user_a", SeedTransactionSource()) == []
    assert client.calls == []


# ----- batch integrity ---------------------------------------------------


@pytest.mark.parametrize(
    "payload,exc",
    [
        pytest.param('{"results": [{"match": true, "matched_tx_id": "tx_1", "reason": "ok"}]}',
                     ValueError, id="too-few-results"),
        pytest.param('{"results": [{"match": true, "matched_tx_id": "tx_1", "reason": "ok"},'
                     '{"match": true, "matched_tx_id": "tx_2", "reason": "ok"},'
                     '{"match": true, "matched_tx_id": "tx_3", "reason": "ok"}]}',
                     ValueError, id="too-many-results"),
    ],
)
def test_result_count_mismatch_raises(fake_llm, payload, exc):
    """A length mismatch means the verdict-to-claim mapping is unknowable.
    Raising is correct — the pipeline turns it into a fail-closed refusal."""
    fake_llm(judge=payload)
    claims = [_claim("first"), _claim("second")]
    with pytest.raises(exc):
        verify_transaction_claims(claims, "user_a", SeedTransactionSource())


def test_missing_results_key_raises(fake_llm):
    """A payload with no `results` key is unusable — the guard must reject it."""
    fake_llm(judge='{"decisions": []}')
    with pytest.raises(ValidationError):
        verify_transaction_claims([_claim()], "user_a", SeedTransactionSource())


def test_verifier_makes_exactly_one_call_per_batch(fake_llm):
    """Scripting a single response proves batching: a per-claim implementation
    would exhaust the queue on claim #2."""
    client = fake_llm(judge=['{"results": ['
                             '{"match": true, "matched_tx_id": "tx_1", "reason": "ok"},'
                             '{"match": true, "matched_tx_id": "tx_2", "reason": "ok"}]}'])
    claims = [(_claim("first"), NEEDS), (_claim("second"), NEEDS)]

    _verifier().resolve(claims, "user_a")
    assert len(client.calls_of("judge")) == 1


def test_fake_client_exhaustion_is_loud(fake_llm):
    """Guards the guard: an unexpected extra round-trip fails the test."""
    client = fake_llm(judge=[_match("tx_1")])
    v = _verifier()
    v.resolve([(_claim(), NEEDS)], "user_a")
    with pytest.raises(FakeLlmExhausted):
        v.resolve([(_claim(), NEEDS)], "user_a")
    assert len(client.calls_of("judge")) == 2


# ----- what the model is shown -------------------------------------------


def test_prompt_carries_the_rows_needed_to_judge(fake_llm):
    """Direction, amount, date, counterparty and tx id must all be rendered —
    the four match conditions are unjudgeable if any is missing."""
    client = fake_llm(judge=_match("tx_1"))
    _verifier().resolve([(_claim(), NEEDS)], "user_a")

    user_prompt = client.calls_of("judge")[0].user
    assert "ASKING USER: user_a" in user_prompt
    assert "USER TRANSACTIONS:" in user_prompt
    assert "counterparty:" in user_prompt
    assert "outbound" in user_prompt and "inbound" in user_prompt
    assert "$" in user_prompt
    assert "user_id=user_c" in user_prompt  # counterparty ids resolvable
    # Claim fields the model needs
    assert "asserted subject: user_c" in user_prompt


def test_user_with_no_transactions_gets_explicit_placeholder(fake_llm):
    """An empty tx table must read as 'no transactions', not as an empty prompt
    section the model could mistake for 'unavailable'."""
    class EmptySource:
        def get_user_transactions(self, user_id: str) -> list[dict]:
            return []

    client = fake_llm(judge=_no_match("none"))
    verify_transaction_claims([_claim()], "user_a", EmptySource())

    assert "(no transactions on this user's accounts)" in client.calls_of("judge")[0].user


# ----- SeedTransactionSource fidelity ------------------------------------


def test_seed_source_returns_only_the_users_own_accounts():
    """The scoping guarantee: the verifier can never see another user's rows."""
    from argo.db.seed import accounts_for

    source = SeedTransactionSource()
    for uid in ("user_a", "user_b", "user_c"):
        owned = accounts_for(uid)
        rows = source.get_user_transactions(uid)
        assert rows, f"{uid} should have seeded transactions"
        assert {r["account_id"] for r in rows} <= owned


def test_seed_source_row_shape_matches_the_protocol():
    """Fields the verifier's renderer reads must all be present."""
    rows = SeedTransactionSource().get_user_transactions("user_a")
    required = {
        "id", "account_id", "amount_cents", "direction",
        "counterparty_name", "counterparty_user_id", "memo", "ts",
    }
    assert required <= set(rows[0])
    assert all(r["direction"] in ("inbound", "outbound") for r in rows)


def test_seed_source_is_ordered_by_timestamp():
    rows = SeedTransactionSource().get_user_transactions("user_a")
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
