# Argo

The open-source runtime gate that stops your LLM from telling the wrong user the right answer.

**Status:** Phase 0 — pre-build, active development. Not production-ready.

## What it does

Argo sits between your application and your LLM. For every response the LLM generates, Argo:

1. **Extracts claims.** A small-LLM judge breaks the response into discrete factual claims, each tied to a source span.
2. **Checks entitlements.** For each claim, evaluates `(user, claim) → allow | redact | block` against the user's entitlement bundle.
3. **Rewrites the response.** Unauthorized claims are redacted; authorized claims pass through.
4. **Logs everything.** Every claim, every verdict, every citation is appended to a regulator-readable audit log.

Argo solves exactly **OWASP LLM Top 10 #2 — Sensitive Information Disclosure**. Nothing else.

## Quick start

### Full demo (Docker)

```bash
cp .env.example .env          # add your Anthropic API key
docker compose up -d --build  # Postgres + gateway
```

Open **http://localhost:8000/ui** — the split-screen demo: a naive LLM
baseline (no gating) beside Argo's gated output, with the per-claim audit
trail underneath. Seven scripted demo queries are one click away.

### Local development

```bash
docker compose up -d postgres   # Postgres only
cp .env.example .env            # add your Anthropic API key
uv sync                         # install dependencies (incl. dev)
uv run uvicorn argo.main:app --reload
uv run pytest                   # run the test suite
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Naive baseline — full data dump to the LLM, no gating. The "before" half of the demo. |
| `POST /chat/argo` | Gated path — extract → entitlement-check → source-verify → rewrite → audit. |
| `GET /audit/recent` | Recent audit events for the audit panel. |
| `GET /health` | Liveness probe. |

## How it works

The gated path runs six stages (`argo/pipeline.py`):

1. **Naive chat** (`argo/naive.py`) — the LLM answers with full data in context.
2. **Claim extraction** (`argo/judge.py`) — a Haiku judge breaks the response into discrete claims, each with a `subject`, `type`, `role`, and `source_span`.
3. **Entitlement check** (`argo/entitlements.py`) — each claim is scored against the asking user's bundle: `ALLOW | BLOCK | NEEDS_SOURCE_CHECK`.
4. **Source-span verification** (`argo/verifier.py`) — transaction claims are checked against the user's actual data; hallucinated or misattributed claims become `REDACT`.
5. **Rewrite** (`argo/rewriter.py`) — disallowed spans are redacted; an over-redacted response is swapped for a refusal.
6. **Audit** (`argo/db/audit.py`) — every claim, verdict, and reason is persisted.

## Project layout

```
argo/               # gateway package — FastAPI app + the six pipeline stages
argo/db/            # Postgres access, schema, seed data, audit log
static/             # demo UI (single-file vanilla HTML/CSS/JS)
tests/              # pytest suite (entitlement policy + rewriter)
scripts/            # eval + validation harnesses
eval/               # labeled claim set + baseline characterization
docker-compose.yml  # Postgres + gateway
Dockerfile          # gateway image
```

## License

Apache 2.0. See [LICENSE](LICENSE).
