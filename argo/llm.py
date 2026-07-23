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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


# ----- FakeLlmClient: deterministic client for tests / offline dev ------


class FakeLlmClient:
    """A controllable `LlmClient` that never touches the network.

    Ships in the production module on purpose: it is the reference
    implementation of the `LlmClient` Protocol, so a bank writing its own
    client has a minimal example to read, and Argo's own tests can exercise
    every LLM-dependent stage (extractor, verifier, naive chat) with no API
    key and no network.

    Script it three ways — each accepted by both `chat=` and `judge=`:

        FakeLlmClient(judge='{"claims": []}')             # same text every call
        FakeLlmClient(judge=['{"claims": []}', '{...}'])  # queue, in call order
        FakeLlmClient(judge=lambda system, user: "...")   # computed per call

    A queue that runs dry raises `FakeLlmExhausted`, so a test that triggers
    more LLM round-trips than it scripted fails loudly rather than silently
    replaying the last response. Passing an `Exception` instance (or a
    callable that raises) simulates a provider failure.

    Every call is appended to `.calls` as a `FakeCall`, so tests can assert
    on prompt contents and on how many round-trips a path actually made —
    the verifier's "skip the LLM when nothing needs checking" optimisation
    is only testable this way.

    Constructs with zero arguments so it also works as a plugin spec:
    `LLM_CLIENT=argo.llm:FakeLlmClient` gives a gateway that boots and
    answers without an LLM provider (useful for wiring-only smoke tests).
    """

    def __init__(
        self,
        chat: _Script | None = None,
        judge: _Script | None = None,
        *,
        latency_ms: int = 0,
    ) -> None:
        self._chat = _normalize_script(chat if chat is not None else _DEFAULT_CHAT, "chat")
        self._judge = _normalize_script(judge if judge is not None else _DEFAULT_JUDGE, "judge")
        self._latency_ms = latency_ms
        self.calls: list[FakeCall] = []

    def chat(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
        return self._record("chat", self._chat, system, user, max_tokens)

    def judge(self, system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
        return self._record("judge", self._judge, system, user, max_tokens)

    def _record(
        self, kind: str, script: _ScriptFn, system: str, user: str, max_tokens: int
    ) -> tuple[str, int]:
        self.calls.append(FakeCall(kind=kind, system=system, user=user, max_tokens=max_tokens))
        return script(system, user), self._latency_ms

    # Convenience accessors for assertions.

    def calls_of(self, kind: str) -> list[FakeCall]:
        return [c for c in self.calls if c.kind == kind]


@dataclass(frozen=True)
class FakeCall:
    """One recorded call against a `FakeLlmClient`."""

    kind: str  # "chat" | "judge"
    system: str
    user: str
    max_tokens: int


class FakeLlmExhausted(RuntimeError):
    """A scripted response queue ran out before the code stopped calling."""


_ScriptFn = Callable[[str, str], str]
_Script = str | Exception | Sequence[str | Exception] | _ScriptFn

_DEFAULT_CHAT = "This is a fake chat response."
_DEFAULT_JUDGE = '{"claims": []}'


def _normalize_script(script: _Script, label: str) -> _ScriptFn:
    """Collapse the three scripting forms into one callable."""
    if callable(script):
        return script

    if isinstance(script, (str, Exception)):
        item = script

        def constant(_system: str, _user: str) -> str:
            return _unwrap(item)

        return constant

    queue = list(script)
    remaining = iter(queue)

    def from_queue(_system: str, _user: str) -> str:
        try:
            return _unwrap(next(remaining))
        except StopIteration:
            raise FakeLlmExhausted(
                f"FakeLlmClient.{label} was called more times than scripted "
                f"({len(queue)} response(s) provided). Either the code under test "
                f"made an unexpected extra LLM round-trip, or the script is short."
            ) from None

    return from_queue


def _unwrap(item: str | Exception) -> str:
    if isinstance(item, Exception):
        raise item
    return item


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
    "FakeLlmClient",
    "FakeCall",
    "FakeLlmExhausted",
    "call_chat_model",
    "call_judge_model",
]
