# Contributing to Argo

Thanks for your interest. Argo is a security control with a deliberately
narrow scope — it gates LLM output against the asking user's entitlements, and
nothing else. Contributions that sharpen that job are very welcome;
contributions that widen it will likely be declined. See
[PRD.md §7](PRD.md) for what Argo is explicitly *not*.

**Status:** Phase 0, pre-production. Interfaces move without deprecation
cycles. If you are planning something substantial, open an issue first.

## Code of conduct

Be respectful and constructive. Assume good faith.

## Getting started

### Prerequisites

- **Python 3.11+** (3.11, 3.12 and 3.13 are tested in CI)
- **[uv](https://docs.astral.sh/uv/)** — `brew install uv` or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Docker** — for Postgres and the demo stack
- **Make**

### Setup

```bash
git clone https://github.com/<you>/argo.git
cd argo
cp .env.example .env      # add your ANTHROPIC_API_KEY for the demo/eval paths
make install
```

`make help` lists every target. You do not need an API key or a database to
run the test suite — only to run the demo or the eval harnesses.

### Running it

```bash
make run       # gateway + Postgres, hot reload → http://localhost:8000/ui
make demo      # full stack in Docker, closest to what users get
```

## Development workflow

1. **Fork** and branch from `main`.
2. **Make a focused change**, aligned with existing module boundaries.
3. **Run the checks** — see below.
4. **Open a PR** describing what changed and why. Link the issue if there is one.

### Before you push

```bash
make ci        # lint + policy validation + offline tests — what CI runs
```

If you touched anything in `argo/db/` or the entitlement adapters, also run
the live-Postgres suite:

```bash
make db-up
make test-db
```

CI runs the same gates on Python 3.11/3.12/3.13, plus a Docker build and a
compose health check. If `make ci` passes locally, CI should be green.

## Testing

The default suite runs **offline** — no API key, no database, no network.
Every pluggable seam has a test double, so the full gating pipeline is
exercised without infrastructure:

| Seam | Double |
|---|---|
| LLM provider | `FakeLlmClient` (`argo/llm.py`) |
| Entitlement source | `SeedDerivedAdapter` (`argo/entitlements.py`) |
| Audit destination | `InMemoryAuditWriter` (`argo/db/audit.py`) |
| Transaction source | `SeedTransactionSource` (`argo/db/seed.py`) |

`tests/conftest.py` wires them together. The `gated` fixture runs the whole
pipeline offline:

```python
def test_third_party_balance_is_blocked(gated):
    result, _client, _writer = gated(
        user_id="user_a",
        raw_response="Your balance is $4,250.00. Bob's balance is $12,890.00.",
        judge='{"claims": [...]}',      # scripted extractor response
    )
    assert "$12,890.00" not in result.final_response
```

Tests that need a real database are marked `@pytest.mark.db` and skip unless
`TEST_DATABASE_URL` is set. They cover the production data path —
`DbDerivedAdapter`, `argo/db/queries.py`, `PostgresAuditWriter` — which the
offline suite deliberately substitutes away.

**What tests should and should not assert.** Unit tests pin plumbing and
fail-closed behaviour. They do not assert what a model will say — prompt
quality is measured by the harnesses in `scripts/` against the live model
(`make eval-extractor`, `make eval-verifier`, `make eval-demo`). Those cost
money and are never part of `make ci`.

## What a good PR looks like

- **Security-relevant changes come with a test that fails without them.** If
  you change redaction, verdicts, or the entitlement engine, show the test
  catching the old behaviour.
- **Fail closed.** Every new branch should degrade to redaction or refusal,
  never to passing the raw response through. See the invariants in
  [AGENTS.md](AGENTS.md).
- **Policy changes are explained.** `argo/policy/banking.toml` is a
  GRC-reviewable artifact. Edits to it are policy decisions, and they will move
  the `EXPECTED_VERDICTS` table in `tests/test_entitlements.py` — update it and
  say why in the PR.
- **New pluggable behaviour goes behind a Protocol**, selected by an env var in
  `config.py` and loaded via `argo/plugins.py`, so banks can swap it without
  forking. See [ADAPTERS.md](ADAPTERS.md).
- **Docs move with the code.** `POLICY.md`, `DEPLOYMENT.md`, `ADAPTERS.md` and
  `SECURITY.md` describe behaviour; if you change behaviour, change them.

## Style

`ruff` governs it — `make lint` to check, `make fmt` to autofix. Line length
is 100. A few files with inherently long lines (seed data tables, verbatim
prompt text) are exempted per-file in `pyproject.toml`; prefer that over
scattering `noqa` comments.

Beyond the linter: match the surrounding code. Argo leans on module-level
docstrings that explain why a component exists and what breaks without it —
that context is worth more than inline comments restating the code.

## Reporting security issues

Do **not** open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md#reporting-vulnerabilities).

## License

By contributing you agree your contributions are licensed under Apache 2.0,
matching [LICENSE](LICENSE).
