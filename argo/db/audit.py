"""Audit-log writer + reader.

One row per /chat/argo call. The `claim_audit` jsonb column holds the full
per-claim trail the demo's audit panel renders — text, subject, type, role,
source_span, verdict, reason. Aggregates (whole_blocked, redacted_chars,
latencies) live alongside for fast filtering and the demo summary stats.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from argo.db import get_conn


class AuditedClaim(BaseModel):
    """One row in audit_events.claim_audit (the per-claim trail).

    Flattens the Claim + the post-verifier verdict so the audit panel can
    render a single table without any joins on the client.
    """

    text: str
    subject: str
    type: str
    role: str
    source_span: str
    verdict: str   # ALLOW | BLOCK | REDACT (terminal verdicts only)
    reason: str

    model_config = ConfigDict(extra="forbid")


class AuditEvent(BaseModel):
    """Insert payload — id and ts are filled in by Postgres."""

    user_id: str
    query: str
    raw_response: str
    final_response: str
    whole_blocked: bool
    redacted_chars: int = 0
    claim_audit: list[AuditedClaim] = Field(default_factory=list)
    chat_model: str
    chat_latency_ms: int = 0
    extractor_latency_ms: int = 0
    verifier_latency_ms: int = 0


class AuditEventRecord(AuditEvent):
    """Read shape — includes the persisted id + ts."""

    id: int
    ts: datetime


def write_audit_event(event: AuditEvent) -> int:
    """Persist one audit row. Returns the assigned id."""
    claim_audit_json = json.dumps([c.model_dump() for c in event.claim_audit])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_events (
              user_id, query, raw_response, final_response, whole_blocked,
              redacted_chars, claim_audit, chat_model, chat_latency_ms,
              extractor_latency_ms, verifier_latency_ms
            ) VALUES (
              %s, %s, %s, %s, %s,
              %s, %s::jsonb, %s, %s,
              %s, %s
            ) RETURNING id
            """,
            (
                event.user_id, event.query, event.raw_response, event.final_response,
                event.whole_blocked, event.redacted_chars, claim_audit_json,
                event.chat_model, event.chat_latency_ms,
                event.extractor_latency_ms, event.verifier_latency_ms,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["id"])


def get_recent_audit_events(
    user_id: str | None = None, *, limit: int = 50
) -> list[AuditEventRecord]:
    """Fetch recent audit rows for the demo panel.

    user_id=None returns all users' events (admin view). When the user is
    specified, returns just their events. Newest first.
    """
    where = "WHERE user_id = %s" if user_id else ""
    params: tuple = (user_id, limit) if user_id else (limit,)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, ts, user_id, query, raw_response, final_response,
                   whole_blocked, redacted_chars, claim_audit,
                   chat_model, chat_latency_ms, extractor_latency_ms,
                   verifier_latency_ms
            FROM audit_events
            {where}
            ORDER BY ts DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

    out: list[AuditEventRecord] = []
    for r in rows:
        out.append(
            AuditEventRecord(
                id=r["id"],
                ts=r["ts"],
                user_id=r["user_id"],
                query=r["query"],
                raw_response=r["raw_response"],
                final_response=r["final_response"],
                whole_blocked=r["whole_blocked"],
                redacted_chars=r["redacted_chars"],
                claim_audit=[AuditedClaim(**c) for c in (r["claim_audit"] or [])],
                chat_model=r["chat_model"],
                chat_latency_ms=r["chat_latency_ms"],
                extractor_latency_ms=r["extractor_latency_ms"],
                verifier_latency_ms=r["verifier_latency_ms"],
            )
        )
    return out
