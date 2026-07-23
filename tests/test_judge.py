"""Tests for argo.judge — the claim extractor's parsing and fail-closed contract.

The extractor is the pipeline's first LLM stage and its most fragile: it asks a
model for strict JSON and has to survive every way a model can fail to give it.
These tests pin that behaviour with a scripted `FakeLlmClient`, so they run with
no API key and no Postgres.

What matters here is the *parse boundary*, not prompt quality — whether a given
prompt makes Haiku extract the right claims is what `eval/labeled_claims.json`
and `scripts/run_extractor_eval.py` measure against the real model.
"""

from __future__ import annotations

import pytest

from argo.judge import (
    ExtractionError,
    build_extractor_system_prompt,
    extract_claims,
    extract_claims_raw,
)

KNOWN = "USERS:\n- user_a: Alice Chen, alice@example.com"

VALID_PAYLOAD = """{"claims": [
  {"text": "Alice's balance is $4,250.00", "subject": "acct_a_chk", "type": "balance",
   "role": "primary", "source_span": "balance is $4,250.00"}
]}"""


def _extract(fake_llm, response: str, payload, user_id: str = "user_a"):
    client = fake_llm(judge=payload)
    claims = extract_claims(response, user_id, known_entities=KNOWN)
    return claims, client


# ----- the happy path ----------------------------------------------------


def test_valid_json_parses_into_claims(fake_llm):
    claims, _ = _extract(fake_llm, "Your balance is $4,250.00", VALID_PAYLOAD)
    assert len(claims.claims) == 1
    c = claims.claims[0]
    assert c.subject == "acct_a_chk"
    assert c.type == "balance"
    assert c.role == "primary"
    assert c.source_span == "balance is $4,250.00"


def test_empty_claims_array_is_valid(fake_llm):
    """A clean refusal legitimately yields zero claims — not an error."""
    claims, _ = _extract(fake_llm, "I can't share that.", '{"claims": []}')
    assert claims.claims == []


# ----- tolerating the ways models wrap JSON ------------------------------


@pytest.mark.parametrize(
    "wrapped",
    [
        pytest.param(f"```json\n{VALID_PAYLOAD}\n```", id="json-fence"),
        pytest.param(f"```\n{VALID_PAYLOAD}\n```", id="bare-fence"),
        pytest.param(f"  \n{VALID_PAYLOAD}\n  ", id="surrounding-whitespace"),
        pytest.param(
            f"Here are the claims I found:\n{VALID_PAYLOAD}\nLet me know if you need more.",
            id="chatty-preamble-and-epilogue",
        ),
    ],
)
def test_wrapped_json_still_parses(fake_llm, wrapped):
    claims, _ = _extract(fake_llm, "Your balance is $4,250.00", wrapped)
    assert len(claims.claims) == 1
    assert claims.claims[0].subject == "acct_a_chk"


# ----- fail-closed on unparseable output ---------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("not json at all", id="prose"),
        pytest.param("", id="empty-output"),
        pytest.param('{"claims": [', id="truncated-mid-array"),
        pytest.param('{"claims": [{"text": "x", "subject": "user_a"}]}', id="claim-missing-fields"),
        pytest.param(
            '{"claims": [{"text": "x", "subject": "user_a", "type": "not_a_real_type",'
            ' "role": "primary", "source_span": "x"}]}',
            id="claim-type-off-enum",
        ),
        pytest.param(
            '{"claims": [{"text": "x", "subject": "user_a", "type": "balance",'
            ' "role": "bystander", "source_span": "x"}]}',
            id="role-off-enum",
        ),
    ],
)
def test_unparseable_output_raises_extraction_error(fake_llm, bad):
    """`extract_claims` must raise, never return partial claims.

    Returning a half-parsed payload would under-redact: the rewriter only
    redacts spans it was told about, so a dropped claim is a leak.
    """
    fake_llm(judge=bad)
    with pytest.raises(ExtractionError):
        extract_claims("Your balance is $4,250.00", "user_a", known_entities=KNOWN)


def test_raw_variant_returns_none_instead_of_raising(fake_llm):
    """`extract_claims_raw` is the pipeline's entry point — it signals failure
    with None + the raw text so the caller can audit what the model said."""
    fake_llm(judge="not json at all")
    parsed, raw, _latency = extract_claims_raw(
        "Your balance is $4,250.00", "user_a", known_entities=KNOWN
    )
    assert parsed is None
    assert raw == "not json at all"


def test_provider_error_propagates(fake_llm):
    """A transport failure is not a parse failure — it must surface, not be
    silently turned into 'zero claims' (which would pass the response through)."""
    fake_llm(judge=RuntimeError("provider 503"))
    with pytest.raises(RuntimeError, match="provider 503"):
        extract_claims("Your balance is $4,250.00", "user_a", known_entities=KNOWN)


# ----- no wasted round-trips ---------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_response_skips_the_llm_entirely(fake_llm, blank):
    client = fake_llm(judge=VALID_PAYLOAD)
    claims = extract_claims(blank, "user_a", known_entities=KNOWN)
    assert claims.claims == []
    assert client.calls == []

    parsed, raw, latency = extract_claims_raw(blank, "user_a", known_entities=KNOWN)
    assert parsed is not None and parsed.claims == []
    assert (raw, latency) == ("", 0)
    assert client.calls == []


# ----- prompt wiring -----------------------------------------------------


def test_asking_user_and_response_reach_the_prompt(fake_llm):
    client = fake_llm(judge='{"claims": []}')
    extract_claims("Your balance is $4,250.00", "user_b", known_entities=KNOWN)

    call = client.calls_of("judge")[0]
    assert "ASKING USER: user_b" in call.user
    assert "Your balance is $4,250.00" in call.user
    assert KNOWN in call.system


def test_known_entities_are_injected_into_the_system_prompt():
    """No LLM needed — the prompt builder is pure."""
    prompt = build_extractor_system_prompt("USERS:\n- user_z: Zoe Test, zoe@example.com")
    assert "user_z: Zoe Test" in prompt
    assert "{known_entities}" not in prompt  # placeholder actually substituted
