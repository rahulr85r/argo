"""End-to-end /chat/argo pipeline: naive chat → extract → entitlement check
→ source-span verify → rewrite → audit-log.

Returns ArgoChatResponse with both the raw LLM output and the rewritten
text, so the demo's split-screen UI can render naive-vs-Argo side by side
from a single round-trip. Per-claim verdicts + reasons feed the audit panel.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from argo.claims import Claim
from argo.db.audit import AuditEvent, AuditedClaim, write_audit_event
from argo.entitlements import (
    ClaimVerdict,
    HardcodedAdapter,
    UnknownUserError,
    VerdictResult,
    check_claim,
)
from argo.judge import ExtractionError, extract_claims_raw
from argo.naive import naive_chat
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


def run_argo_pipeline(user_id: str, query: str) -> ArgoChatResponse:
    """The full Argo gate. Raises UnknownUserError for an unknown user_id."""
    t_total_start = time.perf_counter()

    # 0. Resolve bundle up-front so an invalid user_id fails fast.
    bundle = HardcodedAdapter().get_bundle(user_id)

    # 1. Naive chat — same code path as the baseline /chat endpoint.
    raw_response, chat_model, chat_ms = naive_chat(user_id, query)

    # 2. Extract claims. Fail-closed: an extractor parse failure swaps the
    #    response for the generic refusal and records the failure in audit.
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

    # 3. Entitlement check (synchronous, no LLM).
    claims_with_verdicts: list[tuple[Claim, VerdictResult]] = [
        (c, check_claim(c, bundle)) for c in parsed.claims
    ]

    # 4. Source-span verifier resolves any NEEDS_SOURCE_CHECK to ALLOW or REDACT.
    t_verify_start = time.perf_counter()
    resolved = resolve_verdicts(claims_with_verdicts, user_id)
    verifier_ms = int((time.perf_counter() - t_verify_start) * 1000)

    # 5. Rewrite the response with terminal verdicts.
    rewrite = rewrite_response(raw_response, resolved)

    # 6. Audit + return.
    return _audit_and_return(
        user_id=user_id, query=query,
        raw_response=raw_response, final_response=rewrite.final_response,
        whole_blocked=rewrite.whole_blocked, redacted_chars=rewrite.redacted_chars,
        claims_with_verdicts=resolved,
        chat_model=chat_model, chat_ms=chat_ms,
        extractor_ms=extractor_ms, verifier_ms=verifier_ms,
        t_total_start=t_total_start,
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
    "run_argo_pipeline",
]
