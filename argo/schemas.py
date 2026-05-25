from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="Verified asking-user identifier (from your auth proxy).")
    query: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    response: str
    model: str
    latency_ms: int


class GateRequest(BaseModel):
    """Input to POST /argo/gate — the production integration point.

    Your chat orchestrator owns the LLM call: it builds the context, picks
    the model, gets the raw response. Argo accepts the raw response and
    runs only the gating stages (extract → check → verify → rewrite →
    audit), returning the gated final_response plus the per-claim audit.
    """

    user_id: str = Field(..., description="Verified asking-user identifier (from your auth proxy).")
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's original prompt. Used by the extractor for context and recorded in audit.",
    )
    raw_response: str = Field(
        ...,
        min_length=1,
        description="The LLM's full response text. This is what Argo gates.",
    )
    chat_model: str = Field(
        default="external",
        description="Identifier of the model that produced raw_response. Recorded in audit.",
    )
    chat_latency_ms: int = Field(
        default=0,
        ge=0,
        description="Latency of the upstream chat call, if you want it recorded for end-to-end timing.",
    )
