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

```bash
docker compose up -d        # starts Postgres
cp .env.example .env        # add your Anthropic API key
uv sync                     # install dependencies
uv run uvicorn argo.main:app --reload
```

Open http://localhost:8000/health to confirm.

## Project layout

```
argo/           # gateway package (FastAPI app, claim extractor, entitlement checker)
static/         # demo UI (vanilla HTML + JS)
tests/          # pytest suite + claim-extraction eval set
docker-compose.yml  # Postgres
```

## License

Apache 2.0. See [LICENSE](LICENSE).
