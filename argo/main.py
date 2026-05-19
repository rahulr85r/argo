from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from argo.db.bootstrap import init_db
from argo.naive import naive_chat
from argo.schemas import ChatRequest, ChatResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Argo", version="0.0.1", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Naive baseline: full data dump in system prompt, no entitlement gating.

    This is the "before Argo" demo half. Replaced by the gated pipeline in W3.
    """
    try:
        response, model, latency = naive_chat(req.user_id, req.query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ChatResponse(response=response, model=model, latency_ms=latency)


app.mount("/ui", StaticFiles(directory="static", html=True), name="static")
