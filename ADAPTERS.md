# Writing your own adapters

Argo has four pluggable seams. Each is a Python `Protocol`. Replacing one
means writing a class that satisfies the contract and pointing an env var
at it — no forking, no Argo code changes.

This document walks through each Protocol, when you'd write a custom one,
and a worked Okta example end-to-end (because that's the most common
"I need to integrate with our real IDP" case).

| Protocol | Default | Most common reason to replace |
|---|---|---|
| `EntitlementAdapter` | `argo.entitlements:DbDerivedAdapter` | Your identity & relationships live in Okta / Entra / a custom RBAC system, not in Argo's Postgres |
| `AuditWriter` | `argo.db.audit:PostgresAuditWriter` | You need audit events in Splunk / Datadog / Kafka, not Postgres |
| `LlmClient` | `argo.llm:LiteLlmClient` | You need a self-hosted model, in-VPC Bedrock, or a non-LiteLLM-supported provider |
| `Verifier` | `argo.verifier:LlmVerifier` | You want deterministic SQL matching instead of (or before) an LLM call |

---

## How plug-in selection works

At startup, Argo reads four env vars. Each one is a `"module.path:ClassName"`
spec. The class must construct with no arguments — any configuration the
class needs should come from its own env vars, read in `__init__`.

```bash
ENTITLEMENT_ADAPTER=mybank.argo_plugins:OktaEntitlementAdapter
AUDIT_WRITER=mybank.argo_plugins:SplunkHecAuditWriter
LLM_CLIENT=mybank.argo_plugins:BedrockClient
VERIFIER=mybank.argo_plugins:SqlFirstVerifier
```

Argo imports the module, looks up the class, and calls `cls()`. That's it.
Distribute your plugin as a normal pip package, install it in the same
environment as Argo, and you're done.

If the spec can't import or the class doesn't exist, the gateway fails
fast at startup with a clear error pointing at the problem.

---

## 1. `EntitlementAdapter`

```python
class EntitlementAdapter(Protocol):
    def get_bundle(self, user_id: str) -> EntitlementBundle: ...
```

Given a user_id (the verified identifier from your auth proxy), return an
`EntitlementBundle`:

```python
@dataclass(frozen=True)
class EntitlementBundle:
    user_id: str
    owned_subjects: frozenset[str]        # user_id + account_ids they own
    counterparty_visible: frozenset[str]  # other user_ids they may see as counterparty
    counterparty_fields: frozenset[ClaimType]  # which claim types may surface
```

If the user is unknown, raise `argo.entitlements.UnknownUserError(user_id)`.

### When to write your own

You almost always will. The default `DbDerivedAdapter` reads Argo's own
Postgres. Real banks have:

- Customer identity in Okta / Entra / Ping / Auth0.
- Account ownership in the core banking system.
- Allowed counterparty fields in their data-classification taxonomy.

Your adapter consolidates these into the `EntitlementBundle` shape.

### Performance contract

`get_bundle()` is called once per `POST /chat/argo`. The default adapter
caches results for 30 seconds in-process. **Your adapter should cache too** —
if every chat triggers an Okta API call, you'll hit rate limits and add
latency. Pattern:

```python
class OktaEntitlementAdapter:
    def __init__(self):
        self._cache: dict[str, tuple[float, EntitlementBundle]] = {}
        self._ttl = 30  # seconds
        self._lock = threading.Lock()
        # ... Okta client setup, reads OKTA_DOMAIN / OKTA_API_TOKEN env vars

    def get_bundle(self, user_id: str) -> EntitlementBundle:
        # cache lookup (omitted for brevity — see DbDerivedAdapter for shape)
        ...
        # cache miss → build from Okta
        return self._fetch(user_id)
```

### Worked example: Okta

End-to-end skeleton. Drop into `mybank/argo_plugins/okta.py`:

```python
"""OktaEntitlementAdapter — Argo bundle from Okta groups + core-banking lookup."""

from __future__ import annotations

import os
import threading
import time

import requests

from argo.entitlements import (
    EntitlementBundle,
    UnknownUserError,
)
from argo.policy import POLICY


class OktaEntitlementAdapter:
    def __init__(self):
        self._domain = os.environ["OKTA_DOMAIN"]                # e.g. mybank.okta.com
        self._token = os.environ["OKTA_API_TOKEN"]              # API token with read:users + read:groups
        self._core_banking_url = os.environ["CORE_BANKING_URL"] # bank's internal API
        self._ttl = float(os.environ.get("OKTA_CACHE_TTL", "30"))
        self._cache: dict[str, tuple[float, EntitlementBundle]] = {}
        self._lock = threading.Lock()

    def get_bundle(self, user_id: str) -> EntitlementBundle:
        cached = self._cached(user_id)
        if cached is not None:
            return cached
        bundle = self._fetch(user_id)
        self._store(user_id, bundle)
        return bundle

    def _cached(self, user_id: str) -> EntitlementBundle | None:
        with self._lock:
            entry = self._cache.get(user_id)
            if entry is None:
                return None
            expires, bundle = entry
            if time.monotonic() > expires:
                del self._cache[user_id]
                return None
            return bundle

    def _store(self, user_id: str, bundle: EntitlementBundle) -> None:
        with self._lock:
            self._cache[user_id] = (time.monotonic() + self._ttl, bundle)

    def _fetch(self, user_id: str) -> EntitlementBundle:
        # 1. Verify the user exists in Okta. If not, the auth proxy should
        #    never have forwarded the request — raise UnknownUserError so
        #    the gateway returns HTTP 400.
        okta = requests.get(
            f"https://{self._domain}/api/v1/users/{user_id}",
            headers={"Authorization": f"SSWS {self._token}"},
            timeout=2,
        )
        if okta.status_code == 404:
            raise UnknownUserError(user_id)
        okta.raise_for_status()

        # 2. Owned accounts come from core banking, not Okta. Real banks
        #    keep account ownership in the core, not in the IDP.
        cb = requests.get(
            f"{self._core_banking_url}/customers/{user_id}/accounts",
            timeout=2,
        )
        cb.raise_for_status()
        owned_accounts = {acct["account_id"] for acct in cb.json()}

        # 3. Counterparty graph: also from core banking. Apply the policy
        #    rules to the bank's data (this example only implements the
        #    recent_payment rule; extend as you support more rule types).
        cps: set[str] = set()
        for rule in POLICY.counterparty_rules:
            if rule.type == "recent_payment":
                assert rule.lookback_days is not None
                r = requests.get(
                    f"{self._core_banking_url}/customers/{user_id}/recent-counterparties",
                    params={"lookback_days": rule.lookback_days},
                    timeout=2,
                )
                r.raise_for_status()
                cps |= {cp["customer_id"] for cp in r.json()}
            elif rule.type == "joint_account_co_owner":
                r = requests.get(
                    f"{self._core_banking_url}/customers/{user_id}/joint-co-owners",
                    timeout=2,
                )
                r.raise_for_status()
                cps |= {cp["customer_id"] for cp in r.json()}
            else:
                raise ValueError(f"OktaAdapter does not support rule type: {rule.type}")
        cps.discard(user_id)

        return EntitlementBundle(
            user_id=user_id,
            owned_subjects=frozenset({user_id, *owned_accounts}),
            counterparty_visible=frozenset(cps),
            counterparty_fields=POLICY.counterparty_fields,
        )
```

Package this as `mybank-argo-okta`:

```toml
# pyproject.toml
[project]
name = "mybank-argo-okta"
version = "0.1.0"
dependencies = ["requests", "argo"]   # depends on argo for the Protocol shapes
```

Install alongside Argo in your gateway image (`pip install mybank-argo-okta`)
and set:

```bash
ENTITLEMENT_ADAPTER=mybank.argo_plugins.okta:OktaEntitlementAdapter
OKTA_DOMAIN=mybank.okta.com
OKTA_API_TOKEN=<secret>
CORE_BANKING_URL=https://core.internal/v1
```

Restart the gateway. Done.

---

## 2. `AuditWriter`

```python
class AuditWriter(Protocol):
    def write(self, event: AuditEvent) -> int | None: ...
```

Every `POST /chat/argo` produces one `AuditEvent`. The writer persists it.
Return value is optional (the default Postgres impl returns the row id;
fire-and-forget writers can return `None`).

### When to write your own

When your audit trail needs to live somewhere other than Argo's Postgres.
In practice, that's most production deployments — your bank already has a
SIEM, and forensics teams want every audit event there, not in a separate
DB you have to grant them access to.

### Synchronicity matters

`write()` runs **inside the request path** — the gateway will not return
the user's response until it completes. If your downstream is slow, do
not block:

```python
class SplunkHecAuditWriter:
    def __init__(self):
        self._url = os.environ["SPLUNK_HEC_URL"]
        self._token = os.environ["SPLUNK_HEC_TOKEN"]
        self._queue: queue.Queue = queue.Queue(maxsize=10_000)
        threading.Thread(target=self._flush_loop, daemon=True).start()

    def write(self, event: AuditEvent) -> None:
        try:
            self._queue.put_nowait(event.model_dump())
        except queue.Full:
            # Decide: drop, or block the request. For audit, blocking is
            # usually safer than silently dropping.
            self._queue.put(event.model_dump(), timeout=2)
        return None  # fire-and-forget from the request's perspective

    def _flush_loop(self):
        while True:
            batch = [self._queue.get()]
            # ... batch up to N more from the queue, POST to Splunk HEC ...
```

For durability you want before-response (so the regulator sees every gate
decision), use a synchronous write to a durable queue (Kafka with `acks=all`,
or a Postgres outbox table that a separate process drains). Do **not**
fire-and-forget if the audit trail is regulator-mandated.

### Worked example: Postgres outbox

```python
class PostgresOutboxAuditWriter:
    """Synchronous write to a durable outbox table; a separate worker
    pumps the outbox to Splunk. Guarantees audit durability even if
    Splunk is down — at the cost of one extra DB write per chat."""

    def write(self, event: AuditEvent) -> int:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_outbox (payload) VALUES (%s::jsonb) RETURNING id",
                (event.model_dump_json(),),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row["id"])
```

---

## 3. `LlmClient`

```python
class LlmClient(Protocol):
    def chat(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]: ...
    def judge(self, system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]: ...
```

`chat()` runs the customer-facing naive baseline; `judge()` runs the
extractor and verifier. Both return `(response_text, latency_ms)`.

### When to write your own

- **In-VPC LLM:** for regulated banks, the naive call ships the bank's
  full customer dataset to the LLM provider. If that provider is external,
  you have a data-residency problem. Solution: self-host (vLLM, Bedrock
  with VPC endpoints, etc.) and write a client that talks to your endpoint.
- **Non-LiteLLM-supported provider:** rare but exists.
- **Routing logic:** "chat" goes to a more expensive model, "judge" goes
  to a small distilled model you fine-tuned for claim extraction.

### Performance & failure contract

- Implement your own retry / backoff. The pipeline does not retry on top
  of you.
- Raise on terminal failure. The pipeline's extractor path catches and
  fails closed (returns a refusal + audit row); the chat path lets the
  exception bubble to a 500.
- Latency budget: `chat` is typically 1-3s, `judge` is typically 1-2s.
  Latencies beyond that visibly degrade the UX.

### Worked example: AWS Bedrock with separate chat vs judge models

```python
import boto3
import json
import os
import time


class BedrockClient:
    def __init__(self):
        self._chat_model = os.environ["BEDROCK_CHAT_MODEL"]    # e.g. anthropic.claude-haiku-4-5-…
        self._judge_model = os.environ["BEDROCK_JUDGE_MODEL"]  # could be a smaller / fine-tuned one
        self._client = boto3.client("bedrock-runtime")          # picks up IRSA / instance profile

    def chat(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, int]:
        return self._invoke(self._chat_model, system, user, max_tokens)

    def judge(self, system: str, user: str, *, max_tokens: int = 2048) -> tuple[str, int]:
        return self._invoke(self._judge_model, system, user, max_tokens)

    def _invoke(self, model_id: str, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        t0 = time.perf_counter()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        r = self._client.invoke_model(modelId=model_id, body=json.dumps(body))
        payload = json.loads(r["body"].read())
        text = payload["content"][0]["text"]
        return text, int((time.perf_counter() - t0) * 1000)
```

---

## 4. `Verifier`

```python
class Verifier(Protocol):
    def resolve(
        self,
        claims_with_verdicts: list[tuple[Claim, VerdictResult]],
        user_id: str,
    ) -> list[tuple[Claim, VerdictResult]]: ...
```

Given the entitlement engine's claim list (some terminal `ALLOW`/`BLOCK`,
some still `NEEDS_SOURCE_CHECK`), resolve every `NEEDS_SOURCE_CHECK` to
`ALLOW` or `REDACT`. Non-NEEDS_SOURCE_CHECK verdicts pass through.

### When to write your own

The default `LlmVerifier` runs a batched Haiku call against the user's tx
history. It's flexible (handles paraphrased dates, fuzzy amounts) but it's
non-deterministic and costs an LLM round-trip.

Production reasons to replace:

- **Lower cost / latency:** SQL-first match (deterministic on
  amount/date/direction/counterparty), fall back to LLM only when the SQL
  match is ambiguous.
- **Stricter audit:** a fully deterministic verifier means a regulator can
  re-derive the gate decision from the audit log and the user's data.

### Worked example: SQL-first verifier

```python
from argo.claims import Claim
from argo.db.queries import get_user_transactions
from argo.entitlements import ClaimVerdict, VerdictResult


class SqlFirstVerifier:
    """Try a deterministic SQL match before falling back to an LLM.

    Saves the LLM round-trip for the >80% of claims that match cleanly
    on (amount within $1, date within ±2 days, same counterparty, same direction).
    """

    def __init__(self):
        # Lazy-load the LLM fallback so tests can stub it.
        self._llm = None

    def resolve(self, claims_with_verdicts, user_id):
        out = []
        unresolved: list[tuple[int, Claim]] = []
        for i, (claim, verdict) in enumerate(claims_with_verdicts):
            out.append((claim, verdict))
            if verdict.verdict != ClaimVerdict.NEEDS_SOURCE_CHECK:
                continue
            match = self._try_sql_match(claim, user_id)
            if match is not None:
                out[i] = (claim, VerdictResult(
                    verdict=ClaimVerdict.ALLOW if match else ClaimVerdict.REDACT,
                    reason=f"SQL match: {match}",
                ))
            else:
                unresolved.append((i, claim))

        if unresolved:
            # Fall back to LLM only for the ambiguous ones.
            if self._llm is None:
                from argo.verifier import LlmVerifier
                self._llm = LlmVerifier()
            llm_resolved = self._llm.resolve(
                [(c, VerdictResult(ClaimVerdict.NEEDS_SOURCE_CHECK, "")) for _, c in unresolved],
                user_id,
            )
            for (i, _), (_, v) in zip(unresolved, llm_resolved):
                out[i] = (out[i][0], v)
        return out

    def _try_sql_match(self, claim: Claim, user_id: str) -> bool | None:
        """Returns True (match), False (clean mismatch), or None (ambiguous, defer to LLM)."""
        # ... your deterministic match logic against get_user_transactions(user_id) ...
```

---

## Conformance testing your adapter

A skeleton test that every `EntitlementAdapter` implementation should pass.
Copy this into your plugin package's test suite, parametrize on your
adapter, and ensure it stays green across upgrades:

```python
# tests/test_my_adapter_conformance.py
import pytest

from argo.entitlements import EntitlementBundle, UnknownUserError
from mybank.argo_plugins.okta import OktaEntitlementAdapter


@pytest.fixture
def adapter():
    return OktaEntitlementAdapter()


def test_known_user_returns_bundle(adapter):
    bundle = adapter.get_bundle("known_test_user_id")
    assert isinstance(bundle, EntitlementBundle)
    assert bundle.user_id == "known_test_user_id"
    assert isinstance(bundle.owned_subjects, frozenset)
    assert isinstance(bundle.counterparty_visible, frozenset)
    assert isinstance(bundle.counterparty_fields, frozenset)
    assert bundle.user_id in bundle.owned_subjects   # always
    assert bundle.user_id not in bundle.counterparty_visible  # never


def test_unknown_user_raises(adapter):
    with pytest.raises(UnknownUserError):
        adapter.get_bundle("definitely_does_not_exist_xyz")


def test_bundle_immutable(adapter):
    bundle = adapter.get_bundle("known_test_user_id")
    with pytest.raises(AttributeError):
        bundle.user_id = "other"   # frozen dataclass


def test_cache_returns_identical_object(adapter):
    # Adapters that cache should return the same object on the second call
    # within TTL — sanity check on the cache wiring.
    b1 = adapter.get_bundle("known_test_user_id")
    b2 = adapter.get_bundle("known_test_user_id")
    assert b1 is b2 or b1 == b2
```

Similar conformance tests apply to the other three Protocols. A full
official Test Compatibility Kit (TCK) is on the roadmap; until then, the
default implementations in `argo/` are reference behavior.

---

## Packaging & distribution

Plugins are ordinary pip packages. There is no entry-points registration
because the env-var lookup is direct (`module:Class`). Minimum
`pyproject.toml`:

```toml
[project]
name = "mybank-argo-plugins"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "argo",            # for the Protocol shapes — pin to a major version
    "requests",        # whatever else your plugin needs
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Install during the gateway image build:

```dockerfile
RUN pip install mybank-argo-plugins==0.1.0
```

Then set the env var:

```bash
ENTITLEMENT_ADAPTER=mybank.argo_plugins.okta:OktaEntitlementAdapter
```

If your plugin needs to be private, host it in your bank's PyPI mirror and
add the index URL during install. No Argo-side changes needed.

---

## Common mistakes

1. **Constructor takes arguments.** It can't — the loader calls `cls()`.
   Configuration goes in env vars read by `__init__`, not in constructor
   parameters.

2. **Adapter ignores the cache contract.** Without caching, every chat
   triggers a fresh upstream lookup. Your IDP or core-banking system will
   rate-limit you and latency will be terrible.

3. **`get_bundle` returns a dict instead of `EntitlementBundle`.** Type
   errors will surface deep in `check_claim()` and the failure message
   won't point at your adapter. Always return the exact dataclass.

4. **`AuditWriter.write()` blocks for seconds.** Slow audit writes block
   the user's response. Use the queue pattern above and reserve synchronous
   writes for fast destinations (local Postgres, local Kafka producer).

5. **Custom `Verifier` returns a different list length than the input.**
   The pipeline relies on `len(resolved) == len(input)` and that
   non-NEEDS_SOURCE_CHECK verdicts pass through unchanged. Pre-condition;
   not enforced at runtime.

6. **Custom `LlmClient` doesn't raise on terminal failures.** Returning
   an empty string `("", 0)` will make the extractor "succeed" with zero
   claims, which the pipeline interprets as "nothing to gate, ship the
   raw response." That's a leak. Raise on failure.

---

If you write an adapter for a system that might be useful to other banks
(Okta, Entra, Auth0, OPA, etc.), consider open-sourcing it as a sibling
repo (`argo-okta-adapter`, `argo-entra-adapter`). The ecosystem benefits.
