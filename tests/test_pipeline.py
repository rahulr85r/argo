"""Tests for argo.pipeline.gate_response — the production integration point.

This is the path a bank actually calls (`POST /argo/gate`), and until now it had
no test at all. Every stage is exercised with doubles: seed entitlements, a
scripted LLM, an in-memory audit sink. No API key, no Postgres.

The tests are organised around Argo's four documented fail-closed guarantees,
because those are the claims a compliance reviewer would ask us to evidence:

  1. Extractor parse failure     → generic refusal, whole_blocked, audited
  2. Entitlement denial          → span redacted, never emitted
  3. Verifier rejection          → span redacted (hallucination defence)
  4. Audit-destination failure   → response still served, audit_id=None

Plus the invariant that ties them together: **whatever the pipeline redacts
must not appear in `final_response`**, and every claim must reach the audit
trail regardless of verdict.
"""

from __future__ import annotations

import pytest

from argo.entitlements import UnknownUserError
from argo.pipeline import gate_response
from argo.rewriter import REDACTION_MARKER, REFUSAL_TEXT


def _claims(*entries: str) -> str:
    return '{"claims": [' + ",".join(entries) + "]}"


def _claim_json(subject: str, ctype: str, role: str, span: str, text: str = "c") -> str:
    return (
        f'{{"text": "{text}", "subject": "{subject}", "type": "{ctype}",'
        f' "role": "{role}", "source_span": "{span}"}}'
    )


# ===== Guarantee 1 — extractor parse failure =============================


def test_extractor_parse_failure_returns_refusal(gated):
    raw = "Your balance is $4,250.00 and Bob's is $12,890.00."
    result, _client, writer = gated(raw_response=raw, judge="the model rambled instead of JSON")

    assert result.final_response == REFUSAL_TEXT
    assert result.whole_blocked is True
    assert result.redacted_chars == len(raw)
    # Nothing from the ungated response survives.
    assert "$12,890.00" not in result.final_response
    assert "$4,250.00" not in result.final_response

    # The failure itself is audited — a silent refusal would be unreviewable.
    assert len(writer.events) == 1
    audited = writer.events[0].claim_audit
    assert len(audited) == 1
    assert audited[0].verdict == "REDACT"
    assert "parse failure" in audited[0].text
    assert "could not be parsed" in audited[0].reason
    # The raw response is preserved for the reviewer even though it was suppressed.
    assert writer.events[0].raw_response == raw
    assert writer.events[0].final_response == REFUSAL_TEXT


def test_extractor_failure_costs_only_one_round_trip(gated):
    """No verifier call should follow a failed extraction."""
    _result, client, _writer = gated(raw_response="anything", judge="not json")
    assert len(client.calls_of("judge")) == 1


# ===== Guarantee 2 — entitlement denial ==================================


def test_third_party_balance_is_blocked_end_to_end(gated):
    """user_a asks; response leaks Bob's individual balance → redacted.

    `check_claim` case 3: acct_b_chk is not in user_a's owned_subjects.
    """
    raw = "Your balance is $4,250.00. Bob's balance is $12,890.00."
    result, _client, writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=_claims(
            _claim_json("acct_a_chk", "balance", "primary", "Your balance is $4,250.00"),
            _claim_json("acct_b_chk", "balance", "primary", "Bob's balance is $12,890.00"),
        ),
    )

    assert "$4,250.00" in result.final_response      # own data survives
    assert "$12,890.00" not in result.final_response  # third party does not
    assert REDACTION_MARKER in result.final_response
    assert result.whole_blocked is False

    verdicts = {c.subject: c.verdict for c in result.claim_audit}
    assert verdicts == {"acct_a_chk": "ALLOW", "acct_b_chk": "BLOCK"}
    assert len(writer.events[0].claim_audit) == 2


def test_offlist_counterparty_field_is_blocked(gated):
    """A valid counterparty, but `account_number` is not on the whitelist —
    this is the account-number leak the README's hero SVG depicts."""
    raw = "You sent Charlie $45.00. His account number is 4455-9921-0034."
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=_claims(
            _claim_json("user_c", "account_number", "counterparty",
                        "His account number is 4455-9921-0034"),
        ),
    )

    assert "4455-9921-0034" not in result.final_response
    assert result.claim_audit[0].verdict == "BLOCK"
    assert "whitelist" in result.claim_audit[0].reason


def test_hallucinated_subject_is_blocked(gated):
    """subject='unknown' → BLOCK regardless of type or role (fail closed)."""
    raw = "You sent Diana Wong $120.00 last Tuesday for the yoga retreat."
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=_claims(_claim_json("unknown", "transaction", "counterparty",
                                  "You sent Diana Wong $120.00 last Tuesday")),
    )

    assert "Diana Wong" not in result.final_response
    assert result.claim_audit[0].verdict == "BLOCK"
    assert "hallucination" in result.claim_audit[0].reason


def test_non_counterparty_user_is_blocked(gated):
    """user_h has no transactional link to user_a — the 'wall' the seed builds."""
    raw = "Helen Park banks with us too and her checking is healthy."
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=_claims(_claim_json("user_h", "customer_status", "primary",
                                  "Helen Park banks with us too")),
    )
    assert result.claim_audit[0].verdict == "BLOCK"
    assert "counterparty list" in result.claim_audit[0].reason


# ===== Guarantee 3 — verifier rejection ==================================


def test_unverified_transaction_is_redacted(gated):
    """Entitlement says Charlie is a valid counterparty; the verifier says no
    such row exists. This is the hallucination defence that does not depend on
    policy at all."""
    raw = "Great news — you sent Charlie Rivera $189.00 on May 16 for the ski trip."
    result, client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=[
            _claims(_claim_json("user_c", "transaction", "counterparty",
                                "you sent Charlie Rivera $189.00 on May 16")),
            '{"results": [{"match": false, "matched_tx_id": null,'
            ' "reason": "no row within $1 / +-2 days"}]}',
        ],
    )

    assert "$189.00" not in result.final_response
    assert result.claim_audit[0].verdict == "REDACT"
    assert "no matching tx" in result.claim_audit[0].reason
    # Two round-trips: extraction, then one batched verification.
    assert len(client.calls_of("judge")) == 2
    assert result.timings.verifier_ms >= 0


def test_verified_transaction_is_allowed(gated):
    raw = "You sent Charlie Rivera $45.00 on April 8 for the pizza split."
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=[
            _claims(_claim_json("user_c", "transaction", "counterparty",
                                "You sent Charlie Rivera $45.00 on April 8")),
            '{"results": [{"match": true, "matched_tx_id": "tx_0123",'
            ' "reason": "direction/amount/date/counterparty match"}]}',
        ],
    )

    assert result.final_response == raw          # untouched
    assert REDACTION_MARKER not in result.final_response
    assert result.claim_audit[0].verdict == "ALLOW"
    assert "tx_0123" in result.claim_audit[0].reason  # citation in the audit row


def test_no_transaction_claims_means_no_verifier_call(gated):
    """The documented skip: a response with no tx claim costs one LLM call."""
    _result, client, _writer = gated(
        raw_response="Your checking account is open and in good standing.",
        judge=_claims(_claim_json("acct_a_chk", "account_existence", "primary",
                                  "Your checking account is open")),
    )
    assert len(client.calls_of("judge")) == 1


# ===== Guarantee 4 — audit-destination failure ===========================


def test_audit_write_failure_does_not_break_the_response(gated):
    """The bank's SIEM being down must not take the chatbot down with it."""
    raw = "Your balance is $4,250.00."
    result, _client, _writer = gated(
        raw_response=raw,
        judge=_claims(_claim_json("acct_a_chk", "balance", "primary", raw)),
        audit_fails=True,
    )

    assert result.final_response == raw
    assert result.audit_id is None            # the only visible signal
    assert result.claim_audit                 # in-response trail still populated


def test_audit_failure_still_gates_correctly(gated):
    """Redaction must not depend on the audit write succeeding."""
    raw = "Your balance is $4,250.00. Bob's balance is $12,890.00."
    result, _client, _writer = gated(
        raw_response=raw,
        judge=_claims(
            _claim_json("acct_a_chk", "balance", "primary", "Your balance is $4,250.00"),
            _claim_json("acct_b_chk", "balance", "primary", "Bob's balance is $12,890.00"),
        ),
        audit_fails=True,
    )
    assert "$12,890.00" not in result.final_response
    assert result.audit_id is None


# ===== Cross-cutting invariants ==========================================


def test_no_redacted_span_survives_into_the_final_response(gated):
    """The core safety property, asserted structurally rather than per-case."""
    raw = (
        "Your balance is $4,250.00. Bob's balance is $12,890.00. "
        "Charlie's email is charlie@example.com. Helen Park is a customer."
    )
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response=raw,
        judge=_claims(
            _claim_json("acct_a_chk", "balance", "primary", "Your balance is $4,250.00"),
            _claim_json("acct_b_chk", "balance", "primary", "Bob's balance is $12,890.00"),
            _claim_json("user_c", "contact_email", "counterparty",
                        "Charlie's email is charlie@example.com"),
            _claim_json("user_h", "customer_status", "primary", "Helen Park is a customer"),
        ),
    )

    for entry in result.claim_audit:
        if entry.verdict in ("BLOCK", "REDACT"):
            assert entry.source_span not in result.final_response, (
                f"{entry.verdict} span leaked into output: {entry.source_span!r}"
            )


def test_every_claim_reaches_the_audit_trail(gated):
    """Allowed claims are audited too — the log is the regulator's artifact."""
    raw = "Your balance is $4,250.00. Bob's balance is $12,890.00."
    result, _client, writer = gated(
        raw_response=raw,
        judge=_claims(
            _claim_json("acct_a_chk", "balance", "primary", "Your balance is $4,250.00"),
            _claim_json("acct_b_chk", "balance", "primary", "Bob's balance is $12,890.00"),
        ),
    )

    assert len(result.claim_audit) == 2
    assert len(writer.events[0].claim_audit) == 2
    assert {c.verdict for c in writer.events[0].claim_audit} == {"ALLOW", "BLOCK"}
    assert all(c.reason for c in writer.events[0].claim_audit)


def test_clean_response_passes_through_untouched(gated):
    """No false positives on an all-own-data answer."""
    raw = "Your Alice Checking balance is $4,250.00 and the account is in good standing."
    result, _client, _writer = gated(
        raw_response=raw,
        judge=_claims(
            _claim_json("acct_a_chk", "balance", "primary",
                        "Your Alice Checking balance is $4,250.00"),
            _claim_json("acct_a_chk", "account_existence", "primary",
                        "the account is in good standing"),
        ),
    )

    assert result.final_response == raw
    assert result.whole_blocked is False
    assert result.redacted_chars == 0


def test_empty_claim_list_leaves_response_alone(gated):
    """A genuine refusal from the upstream model extracts to zero claims."""
    raw = "I'm not able to share that. Anything else about your accounts?"
    result, _client, writer = gated(raw_response=raw, judge='{"claims": []}')

    assert result.final_response == raw
    assert result.claim_audit == []
    assert len(writer.events) == 1  # still audited


# ===== Request metadata ==================================================


def test_unknown_user_fails_before_any_llm_call(gated):
    """Stage 0 short-circuits — an invalid user_id must not burn a round-trip."""
    from argo.db.seed import SeedTransactionSource  # noqa: F401  (fixture wiring)

    with pytest.raises(UnknownUserError):
        gated(user_id="user_does_not_exist", raw_response="anything",
              judge='{"claims": []}')


def test_audit_records_the_upstream_model_and_latency(gated, seed_backends, fake_llm, audit_sink):
    """`/argo/gate` callers pass their own model id and latency for the audit row."""
    fake_llm(judge='{"claims": []}')
    writer = audit_sink()

    result = gate_response(
        user_id="user_a", query="what's my balance?",
        raw_response="Nothing to report.",
        chat_model="bank-internal/gpt-4o", chat_ms=812,
    )

    assert result.chat_model == "bank-internal/gpt-4o"
    assert result.timings.chat_ms == 812
    assert writer.events[0].chat_model == "bank-internal/gpt-4o"
    assert writer.events[0].chat_latency_ms == 812
    assert writer.events[0].query == "what's my balance?"
    assert writer.events[0].user_id == "user_a"


def test_response_echoes_request_fields(gated):
    result, _client, _writer = gated(
        user_id="user_a", query="my balance?",
        raw_response="Nothing to report.", judge='{"claims": []}',
    )
    assert result.user_id == "user_a"
    assert result.query == "my balance?"
    assert result.raw_response == "Nothing to report."
    assert result.timings.total_ms >= 0
