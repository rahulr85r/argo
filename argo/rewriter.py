"""Splice entitlement+verifier verdicts back into the LLM response.

Inputs are terminal verdicts only — ALLOW, BLOCK, or REDACT. NEEDS_SOURCE_CHECK
should have been resolved by argo.verifier.resolve_verdicts() before reaching
the rewriter; if it slips through, the rewriter fails closed (treats it as
REDACT) so a half-finished pipeline never under-redacts.

Two outputs the gateway cares about:
  - final_response: the user-visible text after redaction (or a generic
    refusal if too much of the response was disallowed)
  - audits: per-claim records for the audit log + demo UI panel
"""

from __future__ import annotations

from dataclasses import dataclass

from argo.claims import Claim
from argo.entitlements import ClaimVerdict, VerdictResult


REDACTION_MARKER = "[redacted]"

# Generic refusal used when too much of a response is disallowed. The demo's
# storytelling beat is "naive leaks → Argo refuses" — keep this clean and
# regulator-friendly.
REFUSAL_TEXT = (
    "I'm not able to share that information. "
    "Is there something else I can help you with about your own accounts?"
)


@dataclass(frozen=True)
class ClaimAudit:
    """One row in the audit log + demo audit panel."""

    claim: Claim
    verdict: ClaimVerdict
    reason: str


@dataclass(frozen=True)
class RewriteResult:
    final_response: str
    audits: list[ClaimAudit]
    whole_blocked: bool
    redacted_chars: int  # before threshold check; useful for the demo audit panel


def _spans_to_redact(claim_verdicts: list[tuple[Claim, VerdictResult]]) -> list[str]:
    """Spans whose final verdict is anything other than ALLOW."""
    out: list[str] = []
    for c, v in claim_verdicts:
        # NEEDS_SOURCE_CHECK reaching the rewriter is a pipeline bug — fail
        # closed by treating it like REDACT rather than letting it through.
        if v.verdict in (ClaimVerdict.BLOCK, ClaimVerdict.REDACT, ClaimVerdict.NEEDS_SOURCE_CHECK):
            out.append(c.source_span)
    return out


def _dedupe_and_drop_subsumed(spans: list[str]) -> list[str]:
    """Drop any span that's a substring of another span (the larger wins).

    Returns longest-first so iterative replace() doesn't break smaller spans
    that happen to lie inside larger ones.
    """
    uniq = list(dict.fromkeys(spans))  # dedupe, preserve order
    uniq.sort(key=len, reverse=True)
    kept: list[str] = []
    for s in uniq:
        if not any(s in larger for larger in kept):
            kept.append(s)
    return kept


def rewrite_response(
    response: str,
    claim_verdicts: list[tuple[Claim, VerdictResult]],
    *,
    block_threshold: float = 0.65,
) -> RewriteResult:
    """Apply verdicts to the response text.

    block_threshold: fraction of the response (by character count) that must
    be marked for redaction before the entire response is swapped for a
    generic refusal. 0.65 keeps Q3 in partial-redaction mode (good demo —
    preserves the legitimate May-12 tx claim) while Q4 / Q6 cleanly trigger
    whole-block.
    """
    audits = [
        ClaimAudit(claim=c, verdict=v.verdict, reason=v.reason)
        for c, v in claim_verdicts
    ]

    raw_spans = _spans_to_redact(claim_verdicts)
    spans = _dedupe_and_drop_subsumed(raw_spans)
    redacted_chars = sum(len(s) for s in spans)

    if not response.strip():
        return RewriteResult(response, audits, whole_blocked=False, redacted_chars=0)

    if redacted_chars / max(1, len(response)) > block_threshold:
        return RewriteResult(
            REFUSAL_TEXT,
            audits,
            whole_blocked=True,
            redacted_chars=redacted_chars,
        )

    rewritten = response
    for span in spans:
        if span and span in rewritten:
            rewritten = rewritten.replace(span, REDACTION_MARKER)

    return RewriteResult(
        rewritten,
        audits,
        whole_blocked=False,
        redacted_chars=redacted_chars,
    )
