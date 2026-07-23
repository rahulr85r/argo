from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from argo.clock import validate_reference_time
from argo.db.audit import AuditEventRecord, get_recent_audit_events
from argo.db.bootstrap import init_db
from argo.entitlements import UnknownUserError
from argo.judge import ExtractionError
from argo.naive import naive_chat
from argo.pipeline import ArgoChatResponse, gate_response, run_argo_pipeline
from argo.schemas import ChatRequest, ChatResponse, GateRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything else: a bad REFERENCE_TIME should stop the gateway here,
    # not on the first request that evaluates a time-windowed rule.
    validate_reference_time()
    init_db()
    yield


app = FastAPI(title="Argo", version="0.0.1", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Naive baseline: full data dump in system prompt, no entitlement gating.

    The "before Argo" half of the split-screen demo. /chat/argo is the gated path.
    """
    try:
        response, model, latency = naive_chat(req.user_id, req.query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ChatResponse(response=response, model=model, latency_ms=latency)


@app.post("/chat/argo", response_model=ArgoChatResponse)
def chat_argo(req: ChatRequest) -> ArgoChatResponse:
    """Demo convenience: own the LLM call + run the gating stages.

    Powers the split-screen UI by returning both the naive raw_response
    and the rewritten final_response from a single round-trip. Production
    integrations should call POST /argo/gate instead — that's the path
    where your own chat orchestrator owns the LLM call and Argo only
    gates the output.
    """
    try:
        return run_argo_pipeline(req.user_id, req.query)
    except UnknownUserError as e:
        raise HTTPException(status_code=400, detail=f"unknown user_id: {e}") from e
    except ExtractionError as e:
        # Fail-closed at the endpoint level too — bubble up as 500 so the
        # demo UI can surface the failure clearly rather than silently
        # rendering a refusal that looks like a normal block.
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/argo/gate", response_model=ArgoChatResponse)
def argo_gate(req: GateRequest) -> ArgoChatResponse:
    """Production integration point: gate a pre-generated LLM response.

    Your chat orchestrator builds the LLM context, makes the LLM call, and
    gets back a raw response. Hand it to Argo via this endpoint and Argo
    runs only the gating stages (extract → entitlement-check → source-
    verify → rewrite → audit). Argo does NOT call the LLM in this path —
    that stays in your control.
    """
    try:
        return gate_response(
            user_id=req.user_id,
            query=req.query,
            raw_response=req.raw_response,
            chat_model=req.chat_model,
            chat_ms=req.chat_latency_ms,
        )
    except UnknownUserError as e:
        raise HTTPException(status_code=400, detail=f"unknown user_id: {e}") from e
    except ExtractionError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/audit/recent", response_model=list[AuditEventRecord])
def audit_recent(
    user_id: str | None = Query(default=None, description="filter to one user; omit for all"),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[AuditEventRecord]:
    """Recent audit events for the demo's live audit panel."""
    return get_recent_audit_events(user_id, limit=limit)


app.mount("/ui", StaticFiles(directory="static", html=True), name="static")
