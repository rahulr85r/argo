"""Thin wrapper around LiteLLM so the rest of the codebase doesn't import it directly."""

import time

import litellm

from argo.config import settings


def call_chat_model(system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
    """Call the configured chat model with a system+user message pair.

    Returns (response_text, latency_ms).
    """
    t0 = time.perf_counter()
    response = litellm.completion(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    text = response.choices[0].message.content or ""
    return text, latency_ms
