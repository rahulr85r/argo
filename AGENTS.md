# AGENTS.md

## Scope

Applies to the whole repository. Keep changes small and aligned with the
existing module boundaries. Argo is a security control: prefer a narrow,
well-tested change over a broad refactor.

## Repository map

- `argo/` — the gateway. `main.py` (FastAPI routes), `pipeline.py` (the six
  gating stages), and one module per stage: `judge.py` (claim extraction),
  `entitlements.py` (policy engine + adapters), `verifier.py` (source-span
  check), `rewriter.py` (redaction), `naive.py` (demo-only baseline chat).
- `argo/db/` — Postgres access (`queries.py`), schema + demo data
  (`schema.sql`, `seed.py`), audit log (`audit.py`).
- `argo/policy/` — `banking.toml` (the GRC-reviewable artifact) and the loader
  that parses it into the `POLICY` singleton.
- `argo/clock.py` — the single reference instant for time-windowed rules.
- `argo/plugins.py` — resolves `"module:Class"` env vars into instances.
- `static/index.html` — the demo UI, single-file vanilla HTML/CSS/JS.
- `tests/` — pytest. `scripts/` — eval harnesses. `eval/` — labeled data.

## Build, run, test

Use the Makefile; it is the single source of truth for commands.

- `make ci` — everything CI runs except the Docker job. Run before proposing a change.
- `make test` — offline suite. No database, no API key, no network.
- `make test-db` — live-Postgres tests. Needs `make db-up` first.
- `make run` — gateway with hot reload at http://localhost:8000/ui
- `make help` — all targets.

Targets marked `[LLM]` in `make help` call a real model and cost money. Never
add them to `make ci` or to the default pytest run.

## Invariants

Breaking one of these is a security regression, not a style issue.

1. **Fail closed.** Every ambiguous path must end in redaction or refusal, not
   pass-through. An unparseable extractor response becomes a refusal; a
   `NEEDS_SOURCE_CHECK` verdict that reaches the rewriter is treated as REDACT;
   a verifier result list whose length does not match its claim list raises.
   If you add a branch, ask what happens when it fails — the answer must not be
   "the raw response is returned".
2. **Whitelist, not blacklist.** `counterparty_fields` enumerates what is
   *allowed*; everything else is denied by default. Never add a deny-list of
   forbidden values — that inverts the security model and requires predicting
   every leak.
3. **Policy is declared in TOML, evaluated in adapters.** `argo/policy/`
   describes rules; it must not evaluate them. Each adapter applies the same
   `POLICY` through its own data source. If you add a rule type, implement it
   in *both* `DbDerivedAdapter` and the seed evaluator, or they will disagree.
4. **One clock.** Time-windowed rules read `argo.clock.reference_now()`. Do not
   reintroduce SQL `NOW()` or `datetime.now()` in a policy path — that is
   exactly how the demo dataset previously decayed out of its own window.
5. **The audit log records every claim**, allowed ones included. It is the
   regulator-facing artifact. An audit-write failure must never break the
   user's response (`audit_id` goes `None` instead).
6. **Never log customer data.** Per `SECURITY.md` §5.2, stdout logs carry
   request metadata and tracebacks only — never the user's query, the LLM
   context blob, or the response text. There is currently no logging inside
   `argo/`; if you add some, keep it to metadata.

## Conventions

- Match the surrounding code. The codebase favours module-level docstrings
  that explain *why* a component exists and what would go wrong without it.
- New pluggable behaviour goes behind a `Protocol` + an env var in
  `config.py`, loaded via `argo/plugins.py`. Implementations must construct
  with zero arguments. See `ADAPTERS.md`.
- Ruff governs style (`make lint`). Line length is 100. Files with inherent
  long lines — seed data tables, verbatim prompt text — are exempted per-file
  in `pyproject.toml`; do not add blanket `noqa`s instead.
- Type annotations on public functions. `from __future__ import annotations`
  at the top of new modules.

## Testing

- Every seam has a test double: `FakeLlmClient` (`argo/llm.py`),
  `InMemoryAuditWriter` (`argo/db/audit.py`), `SeedDerivedAdapter`
  (`argo/entitlements.py`), `SeedTransactionSource` (`argo/db/seed.py`). Use
  them — the default suite must stay offline and credential-free.
- `tests/conftest.py` wires them: `gated` runs the whole pipeline offline;
  `fake_llm` scripts model responses; `live_db` provides real Postgres.
- Tests needing a database are marked `@pytest.mark.db` and skip without
  `TEST_DATABASE_URL`.
- Prompt-quality changes are measured by the `scripts/` eval harnesses against
  the live model, not by unit tests. Unit tests pin plumbing and fail-closed
  behaviour; they do not assert what the model will say.
- When changing entitlement policy, update `EXPECTED_VERDICTS` in
  `tests/test_entitlements.py`. Failures there are the audit trail for policy
  decisions — read them, do not paper over them.

## Things that need extra care

- **`argo/policy/banking.toml`** is a policy artifact. Edits are policy
  changes: explain the rationale, and expect verdict tables to move.
- **Anything touching redaction or verdicts** — pair the change with a test
  that fails without it. `git log` for how existing ones are written.
- **The seed dataset** has fixed dates that the eval fixtures and README
  narrative reference by value ("May 12, 2026", "$45.00"). Changing timestamps
  breaks `eval/labeled_claims.json` and the `scripts/` harnesses.
