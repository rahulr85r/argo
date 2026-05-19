from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="user_a | user_b | user_c")
    query: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    response: str
    model: str
    latency_ms: int
