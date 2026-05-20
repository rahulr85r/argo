"""Thin wrapper around LiteLLM so the rest of the codebase doesn't import it directly."""

import time

import litellm

from argo.config import settings


_RETRYABLE = (
    getattr(litellm, "ServiceUnavailableError", Exception),
    getattr(litellm, "InternalServerError", Exception),
    getattr(litellm, "RateLimitError", Exception),
    getattr(litellm, "APIConnectionError", Exception),
    getattr(litellm, "Timeout", Exception),
)


def _call_with_retry(*, model: str, messages: list[dict], max_tokens: int) -> str:
    """Up to 3 attempts with exponential backoff for transient upstream errors."""
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = litellm.completion(
                model=model,
                messages=messages,
                api_key=settings.anthropic_api_key,
                max_tokens=max_tokens,
            )
            return r.choices[0].message.content or ""
        except _RETRYABLE as e:
            last_exc = e
            if attempt == 2:
                break
            time.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc


def call_chat_model(system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
    """Call the configured chat model with a system+user message pair.

    Returns (response_text, latency_ms). Retries up to 3× on transient
    Anthropic 5xx / connection errors with 1s/2s/4s backoff.
    """
    t0 = time.perf_counter()
    text = _call_with_retry(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return text, int((time.perf_counter() - t0) * 1000)


def call_judge_model(system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
    """Call the configured judge model (claim extractor / verifier).

    Separate from call_chat_model so prompt-iteration on the judge can use a
    different model / params than the chat path. Returns (response_text,
    latency_ms). Same retry behavior as call_chat_model.
    """
    t0 = time.perf_counter()
    text = _call_with_retry(
        model=settings.judge_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return text, int((time.perf_counter() - t0) * 1000)
