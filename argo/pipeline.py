"""Argo pipeline — two entry points sharing the same gating stages.

`gate_response()` is the **production integration point**: it takes a
pre-generated LLM response and runs only the gating stages (extract →
entitlement-check → source-verify → rewrite → audit). This is what the
`POST /argo/gate` endpoint calls, and it's what real banks integrate
against — their own chat orchestrator builds the LLM context, makes
the LLM call, and hands the raw response to Argo for gating.

`run_argo_pipeline()` is the **demo-shaped wrapper**: it owns the LLM
call too (via `argo.naive.naive_chat`), so the `POST /chat/argo` endpoint
can return both the naive raw response and the gated final response from
a single round-trip — that's what powers the split-screen demo UI. In
production this wrapper is rarely used; the bank already has its own
chat path.

Both paths return the same `ArgoChatResponse` shape so audit semantics
and UI rendering are identical regardless of entry point.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from argo.claims import Claim
from argo.db.audit import AuditEvent, AuditedClaim, write_audit_event
from argo.config import settings
from argo.entitlements import (
    ClaimVerdict,
    EntitlementAdapter,
    UnknownUserError,
    VerdictResult,
    check_claim,
)
from argo.judge import ExtractionError, extract_claims_raw
from argo.naive import naive_chat
from argo.plugins import load_plugin
from argo.rewriter import REFUSAL_TEXT, rewrite_response
from argo.verifier import resolve_verdicts


# ----- API response shapes -----------------------------------------------


class ClaimAuditOut(BaseModel):
    text: str
    subject: str
    type: str
    role: str
    source_span: str
    verdict: str
    reason: str


class PipelineTimings(BaseModel):
    chat_ms: int
    extractor_ms: int
    verifier_ms: int
    total_ms: int


class ArgoChatResponse(BaseModel):
    user_id: str
    query: str
    raw_response: str        # naive LLM output, for split-screen comparison
    final_response: str       # post-Argo rewriter
    whole_blocked: bool
    redacted_chars: int
    claim_audit: list[ClaimAuditOut] = Field(default_factory=list)
    audit_id: int | None = None
    timings: PipelineTimings
    chat_model: str


# ----- pipeline ----------------------------------------------------------


# Module-level adapter so any in-process cache survives across requests.
# The class is selected at startup by ARGO_ENTITLEMENT_ADAPTER; defaults
# to argo.entitlements:DbDerivedAdapter. See ADAPTERS.md for swap-in.
_ENTITLEMENT_ADAPTER: EntitlementAdapter = load_plugin(settings.entitlement_adapter)  # type: ignore[assignment]


def gate_response(
    user_id: str,
    query: str,
    raw_response: str,
    *,
    chat_model: str = "external",
    chat_ms: int = 0,
) -> ArgoChatResponse:
    """Gate a pre-generated LLM response. Production integration point.

    Args:
        user_id:      Verified asking-user identifier (from your auth proxy).
        query:        The user's original prompt — used by the extractor for
                      context and recorded in the audit row.
        raw_response: The LLM's full response text. This is the artifact Argo
                      gates; Argo does NOT call the LLM in this path.
        chat_model:   Identifier of the model that produced `raw_response`.
                      Recorded in the audit row. Default `"external"` covers
                      the case where the bank's own chat orchestrator made
                      the call and Argo can't introspect the model id.
        chat_ms:      Latency of the upstream chat call, if you want it
                      recorded for end-to-end timing visibility. Default 0.

    Returns:
        ArgoChatResponse — same shape as run_argo_pipeline. `raw_response`
        in the response echoes back the input so the audit row carries the
        full before/after pair.

    Raises:
        UnknownUserError: `user_id` not in the entitlement adapter's universe.
    """
    t_total_start = time.perf_counter()

    # 0. Resolve bundle up-front so an invalid user_id fails fast.
    bundle = _ENTITLEMENT_ADAPTER.get_bundle(user_id)

    # 1. Extract claims. Fail-closed on parse failure.
    parsed, _raw_extractor, extractor_ms = extract_claims_raw(raw_response, user_id)
    if parsed is None:
        return _audit_and_return(
            user_id=user_id, query=query,
            raw_response=raw_response, final_response=REFUSAL_TEXT,
            whole_blocked=True, redacted_chars=len(raw_response),
            claims_with_verdicts=[],
            chat_model=chat_model, chat_ms=chat_ms,
            extractor_ms=extractor_ms, verifier_ms=0,
            t_total_start=t_total_start,
            extractor_failure=True,
        )

    # 2. Entitlement check (synchronous, no LLM).
    claims_with_verdicts: list[tuple[Claim, VerdictResult]] = [
        (c, check_claim(c, bundle)) for c in parsed.claims
    ]

    # 3. Source-span verifier resolves any NEEDS_SOURCE_CHECK to ALLOW or REDACT.
    t_verify_start = time.perf_counter()
    resolved = resolve_verdicts(claims_with_verdicts, user_id)
    verifier_ms = int((time.perf_counter() - t_verify_start) * 1000)

    # 4. Rewrite the response with terminal verdicts.
    rewrite = rewrite_response(raw_response, resolved)

    # 5. Audit + return.
    return _audit_and_return(
        user_id=user_id, query=query,
        raw_response=raw_response, final_response=rewrite.final_response,
        whole_blocked=rewrite.whole_blocked, redacted_chars=rewrite.redacted_chars,
        claims_with_verdicts=resolved,
        chat_model=chat_model, chat_ms=chat_ms,
        extractor_ms=extractor_ms, verifier_ms=verifier_ms,
        t_total_start=t_total_start,
    )


def run_argo_pipeline(user_id: str, query: str) -> ArgoChatResponse:
    """Demo-shaped wrapper: own the LLM call, then gate the response.

    Calls `argo.naive.naive_chat` to produce a raw_response from the bank's
    full customer dataset, then delegates to `gate_response()` for stages
    1-5. Used by `POST /chat/argo` to power the split-screen UI; production
    integrations should call `gate_response()` directly via `/argo/gate`.
    """
    raw_response, chat_model, chat_ms = naive_chat(user_id, query)
    return gate_response(
        user_id=user_id,
        query=query,
        raw_response=raw_response,
        chat_model=chat_model,
        chat_ms=chat_ms,
    )


def _audit_and_return(
    *,
    user_id: str,
    query: str,
    raw_response: str,
    final_response: str,
    whole_blocked: bool,
    redacted_chars: int,
    claims_with_verdicts: list[tuple[Claim, VerdictResult]],
    chat_model: str,
    chat_ms: int,
    extractor_ms: int,
    verifier_ms: int,
    t_total_start: float,
    extractor_failure: bool = False,
) -> ArgoChatResponse:
    audit_rows = [
        AuditedClaim(
            text=c.text, subject=c.subject, type=c.type, role=c.role,
            source_span=c.source_span,
            verdict=v.verdict.value, reason=v.reason,
        )
        for c, v in claims_with_verdicts
    ]
    if extractor_failure:
        audit_rows = [
            AuditedClaim(
                text="<extractor parse failure — fail-closed REDACT>",
                subject="unknown", type="other", role="primary",
                source_span="", verdict=ClaimVerdict.REDACT.value,
                reason="extractor output could not be parsed; response replaced with refusal",
            )
        ]

    try:
        audit_id: int | None = write_audit_event(
            AuditEvent(
                user_id=user_id, query=query,
                raw_response=raw_response, final_response=final_response,
                whole_blocked=whole_blocked, redacted_chars=redacted_chars,
                claim_audit=audit_rows,
                chat_model=chat_model,
                chat_latency_ms=chat_ms,
                extractor_latency_ms=extractor_ms,
                verifier_latency_ms=verifier_ms,
            )
        )
    except Exception:
        # Audit write failure should not break the user's response. Phase 1
        # adds a retry queue; Phase 0 just notes a None audit_id.
        audit_id = None

    total_ms = int((time.perf_counter() - t_total_start) * 1000)
    return ArgoChatResponse(
        user_id=user_id, query=query,
        raw_response=raw_response, final_response=final_response,
        whole_blocked=whole_blocked, redacted_chars=redacted_chars,
        claim_audit=[
            ClaimAuditOut(**c.model_dump()) for c in audit_rows
        ],
        audit_id=audit_id,
        timings=PipelineTimings(
            chat_ms=chat_ms, extractor_ms=extractor_ms,
            verifier_ms=verifier_ms, total_ms=total_ms,
        ),
        chat_model=chat_model,
    )


__all__ = [
    "ArgoChatResponse",
    "ClaimAuditOut",
    "PipelineTimings",
    "UnknownUserError",
    "ExtractionError",
    "gate_response",
    "run_argo_pipeline",
]
