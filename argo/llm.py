"""LLM client Protocol + LiteLLM default implementation.

The pipeline calls `.chat()` for the customer-facing naive baseline and
`.judge()` for the structured extractor and verifier prompts. They are
separate methods so a deployment can route them to different models —
or different providers entirely — without code changes.

To plug in your own LLM (self-hosted, AWS Bedrock, vLLM, internal-only
model), implement the `LlmClient` Protocol and point `LLM_CLIENT`
at `your_module:YourClass`. See ADAPTERS.md for a worked example.
"""

from __future__ import annotations

import time
from typing import Protocol

import litellm

from argo.config import settings
from argo.plugins import load_plugin


class LlmClient(Protocol):
    """The seam every LLM provider must implement.

    Both methods take a system + user prompt pair and return
    (response_text, latency_ms). Implementations are responsible for
    their own retry / backoff policy and for raising on terminal
    failures (the pipeline turns extractor failure into a fail-closed
    refusal, so raising is safe).
    """

    def chat(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
        """Customer-facing chat completion. Used by the naive baseline."""
        ...

    def judge(self, system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
        """Structured-output completion. Used by the extractor and verifier."""
        ...


# ----- LiteLlmClient: default implementation ----------------------------


_RETRYABLE = (
    getattr(litellm, "ServiceUnavailableError", Exception),
    getattr(litellm, "InternalServerError", Exception),
    getattr(litellm, "RateLimitError", Exception),
    getattr(litellm, "APIConnectionError", Exception),
    getattr(litellm, "Timeout", Exception),
)


class LiteLlmClient:
    """Default `LlmClient` — both methods route through LiteLLM.

    Model selection comes from `settings.chat_model` / `settings.judge_model`
    (env vars `CHAT_MODEL` / `JUDGE_MODEL`). LiteLLM accepts the same string
    format for every provider it supports, so switching providers is usually
    a single env-var change. Default is `anthropic/claude-haiku-4-5` for
    both; AWS Bedrock would be `bedrock/anthropic.claude-haiku-4-5`, etc.

    Retries up to 3 times with 1s/2s/4s backoff on transient 5xx, rate-limit,
    and connection errors. Terminal errors are re-raised.
    """

    def chat(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
        return self._complete(
            model=settings.chat_model, system=system, user=user, max_tokens=max_tokens
        )

    def judge(self, system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
        return self._complete(
            model=settings.judge_model, system=system, user=user, max_tokens=max_tokens
        )

    def _complete(self, *, model: str, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        t0 = time.perf_counter()
        text = self._call_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        return text, int((time.perf_counter() - t0) * 1000)

    @staticmethod
    def _call_with_retry(*, model: str, messages: list[dict], max_tokens: int) -> str:
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


# ----- Module-level singleton + backwards-compatible function shims -----


# Loaded from settings.llm_client; defaults to LiteLlmClient in this module.
# Banks override via env var LLM_CLIENT="their_module:TheirClient".
_CLIENT: LlmClient = load_plugin(settings.llm_client)  # type: ignore[assignment]


def call_chat_model(system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
    """Convenience wrapper around the active LlmClient's `.chat()`."""
    return _CLIENT.chat(system, user, max_tokens=max_tokens)


def call_judge_model(system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
    """Convenience wrapper around the active LlmClient's `.judge()`."""
    return _CLIENT.judge(system, user, max_tokens=max_tokens)


__all__ = [
    "LlmClient",
    "LiteLlmClient",
    "call_chat_model",
    "call_judge_model",
]
