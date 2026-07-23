.DEFAULT_GOAL := help
.PHONY: help install lint fmt test test-db test-all ci run demo demo-down \
        db-up db-down db-shell validate-policy validate-claims \
        eval-extractor eval-verifier eval-demo baseline clean

# Postgres for the `db`-marked tests. Points at the docker-compose service by
# default; override to use your own instance:
#   make test-db TEST_DATABASE_URL=postgres://user:pw@host:5432/dbname
TEST_DATABASE_URL ?= postgres://argo:argo@127.0.0.1:5432/argo

# Everything runs through `uv run`, so no manual venv activation is needed.
# Install uv: https://docs.astral.sh/uv/  (or `brew install uv`)
UV ?= uv

help:  ## Show this help
	@echo "Argo — make targets"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Targets marked [LLM] call a real model and cost money."

# ----- setup -------------------------------------------------------------

install:  ## Install dependencies (incl. dev) into .venv
	$(UV) sync

# ----- quality gates -----------------------------------------------------
#
# `make ci` runs what .github/workflows/ci.yml runs, minus the Docker job.
# If it passes locally it should pass in CI.

lint:  ## Ruff check (no writes)
	$(UV) run ruff check argo tests scripts

fmt:  ## Ruff check with autofix
	$(UV) run ruff check --fix argo tests scripts

test:  ## Offline test suite — no database, no API key, no network
	$(UV) run pytest -q --strict-markers

test-db:  ## Live-Postgres tests (needs `make db-up`)
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" ARGO_REQUIRE_DB=1 \
		$(UV) run pytest -q -m db --strict-markers

test-all: test test-db  ## Both suites

ci: lint validate-policy validate-claims test  ## Everything CI runs, except Docker
	@echo "✓ local CI passed — run 'make test-db' too if Postgres is up"

# ----- validation --------------------------------------------------------

validate-policy:  ## Assert banking.toml parses and is non-empty
	@$(UV) run python -c "\
	from argo.policy import POLICY; \
	assert POLICY.counterparty_fields, 'counterparty_fields is empty'; \
	assert POLICY.counterparty_rules, 'counterparty_rules is empty'; \
	print('policy OK:', sorted(POLICY.counterparty_fields)); \
	print('rules:', [r.type for r in POLICY.counterparty_rules])"

validate-claims:  ## Validate eval/labeled_claims.json against the Claim schema
	$(UV) run python scripts/validate_labeled_claims.py

# ----- running -----------------------------------------------------------

run: db-up  ## Run the gateway with hot reload (http://localhost:8000/ui)
	$(UV) run uvicorn argo.main:app --reload

demo:  ## Full demo stack in Docker — Postgres + gateway
	docker compose up -d --build
	@echo "→ http://localhost:8000/ui"

demo-down:  ## Stop the demo stack and drop its volumes
	docker compose down -v

# ----- database ----------------------------------------------------------

db-up:  ## Start just Postgres
	docker compose up -d postgres

db-down:  ## Stop and remove the Postgres container (keeps its data volume)
	docker compose rm -sf postgres
	@echo "Data volume kept. 'make demo-down' drops it."

db-shell:  ## psql into the dev database
	docker compose exec postgres psql -U argo -d argo

# ----- eval harnesses ----------------------------------------------------
#
# These call the live LLM, so they are deliberately outside `make ci`.
# Requires ANTHROPIC_API_KEY in .env.

eval-extractor:  ## [LLM] Score the claim extractor against labeled_claims.json
	$(UV) run python scripts/run_extractor_eval.py

eval-verifier:  ## [LLM] Smoke-test the source-span verifier
	$(UV) run python scripts/run_verifier_smoke.py

eval-demo:  ## [LLM] Run the 7 scripted demo queries end to end
	$(UV) run python scripts/validate_demo_scenarios.py

baseline:  ## [LLM] Recapture eval/baseline_naive.json from the live model
	$(UV) run python scripts/capture_baseline.py

# ----- housekeeping ------------------------------------------------------

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
