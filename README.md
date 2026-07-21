# Argo

### Compliance won't ship your customer-facing LLM because it might tell the wrong customer the right answer?

### Argo is an open-source runtime gate that checks every LLM response against the asking user's entitlements, redacts what they shouldn't see, and writes a regulator-readable audit row for every claim. Self-hosted. No SaaS. Drops in after your existing IAM.

![Argo: same conversation about a $200 transfer — without Argo the LLM reads out John's account number to Rahul; with Argo only Rahul's own transfer is confirmed](docs/argo-comparison.svg)

**Status:** Phase 0 — pre-build, active development. Not production-ready.

---

## The problem

If you've tried to put an LLM in front of customer-facing data at a bank, a fintech, or anywhere with field-level access rules, you've run into the same wall. Your IAM, your RBAC, your row-level security all do exactly what they were built to do — they decide *which rows* the model is allowed to fetch. They have nothing to say about what the model then *writes about those rows*.

A customer's own transaction row legitimately includes the counterparty's name, the counterparty's bank, the counterparty's account number — all there because the row wouldn't mean anything without them. The model, doing its job, paraphrases. It mentions the counterparty by name. It speculates about a balance it never actually saw. Your IAM log says *"user A fetched their own transactions."* Nothing in it says *"model emitted user B's account number to user A."*

This is **OWASP LLM Top 10 #2 — Sensitive Information Disclosure** — and it's the reason your compliance team keeps saying *not yet* to the LLM your product team has been demoing internally for six months.

Argo exists to fix that, and only that.

---

## What Argo is

Argo sits between your application and your LLM. For every response the LLM generates, Argo:

1. **Extracts claims.** A small-LLM judge breaks the response into discrete factual claims, each tied to a source span.
2. **Checks entitlements.** For each claim, evaluates `(user, claim) → allow | redact | block` against the user's entitlement bundle.
3. **Rewrites the response.** Unauthorized claims are redacted; authorized claims pass through.
4. **Logs everything.** Every claim, every verdict, every citation is appended to a regulator-readable audit log.

Three things keep it adoptable:

- **No model to manage.** The judge is a small Haiku call; everything else is plain Python.
- **No SaaS.** Self-hosted on your own infrastructure, talking only to your existing Postgres and your existing LLM provider.
- **No replacement of your stack.** Argo plugs in *after* your IAM/RBAC has done its job — defense-in-depth at the place LLMs actually leak, which is output synthesis time.

If your LLM only ever sees data the calling user is already allowed to see in full, and your responses are short enough that *"don't put counterparty fields in retrieval"* works as a control, you probably don't need Argo yet. Argo is built for the teams whose compliance reviewer has flagged exactly the gap above, and whose existing access-control stack genuinely can't close it.

---

## Where Argo fits

Existing access controls — IAM, RBAC, MCP tool gates — operate at **tool-call and data-fetch time**: "can the LLM call `get_transactions(user=A)`?" Argo operates at **output synthesis time**: "given a legitimate tool response, does the generated text respect field-level scope?"

Three failure modes upstream gates can't catch on their own:

1. **Counterparty data on legitimately-returned rows.** A user's own transaction row correctly includes the counterparty's name and amount. Whether the model paraphrases over field scope — e.g., reveals a counterparty's account number that happened to be on the row — is a generation-time decision. No upstream gate can predict what the model will say about data it was correctly given.
2. **Hallucinations.** The source-span verifier rejects any claim whose support isn't in retrieved context. This check is **policy-independent** — it answers "is this claim grounded?", not "is this claim allowed?" — so fabricated facts about *any* entity fail closed, regardless of entitlement configuration.
3. **Output-level audit.** IAM logs say "user A fetched their transactions." They don't say "model emitted counterparty's account number, redacted, reason=`field-not-whitelisted`." Argo's audit log is at the claim level.

### Design intent: whitelist, not blacklist

Argo's counterparty policy enumerates what's **allowed**, not what's forbidden: `counterparty_fields = {transaction, account_ownership, aggregate}`. Everything else is denied by default. The bank doesn't need to anticipate every leak — only to audit a small allow-list. In Phase 0 that list is three claim types plus one counterparty-relation rule per user; a reviewer can audit it on one screen.

### What Argo can't do

If the source-of-truth entitlement says `counterparty_visible(A, C) = true` *and* the counterparty whitelist is overly permissive *and* the sensitive data is present in retrieved context — Argo will faithfully enforce the wrong policy. Argo is defense-in-depth on top of correct policy, not a substitute for it. The value it adds is (a) a second checkpoint at the place LLMs actually leak, (b) hallucination defense that doesn't depend on policy at all, and (c) an output-level audit trail.

Adopting Argo requires the bank to author the counterparty-field whitelist — a policy artifact that doesn't exist in any standard IAM system today. The artifact is small (single-digit field counts), derived from existing data classifications, and the `EntitlementAdapter` interface is shaped to compose it from Okta/Entra groups + classification tags rather than hand-authored entries.

## Architecture

```mermaid
flowchart TB
    User(["👤 User"])

    subgraph Client["Client surface"]
        UI["Browser UI<br/><code>static/index.html</code>"]
        Curl["External clients<br/>(curl, scripts)"]
    end

    subgraph Gateway["FastAPI Gateway · <code>argo/main.py</code>"]
        EpHealth["GET /health"]
        EpChat["POST /chat<br/>(naive baseline)"]
        EpArgo["POST /chat/argo<br/>(demo wrapper)"]
        EpGate["POST /argo/gate<br/><b>production integration</b>"]
        EpAudit["GET /audit/recent"]
    end

    subgraph BankChat["Bank's own chat orchestrator (production)"]
        BankLlm["builds context, calls LLM,<br/>hands raw_response to Argo"]
    end

    subgraph Pipeline["Argo Pipeline · <code>argo/pipeline.py</code>"]
        direction TB
        S0["0 · Resolve bundle<br/><b>DbDerivedAdapter</b><br/>30s TTL cache"]
        S1["1 · Naive chat<br/><code>argo/naive.py</code>"]
        S2["2 · Extract claims<br/><code>argo/judge.py</code>"]
        S3["3 · Entitlement check<br/><code>check_claim()</code>"]
        S4["4 · Source-span verify<br/><code>argo/verifier.py</code>"]
        S5["5 · Rewrite<br/><code>argo/rewriter.py</code>"]
        S6["6 · Audit write<br/><code>argo/db/audit.py</code>"]
        S0 --> S1 --> S2 --> S3 --> S4 --> S5 --> S6
    end

    subgraph Policy["Policy · <code>argo/policy/</code>"]
        Toml[("banking.toml<br/>counterparty_fields<br/>counterparty_rules")]
        Loader["__init__.py<br/><b>POLICY</b> singleton"]
        Toml -->|tomllib parse + validate| Loader
    end

    subgraph LLM["Anthropic API · LiteLLM<br/><code>argo/llm.py</code>"]
        Chat["Haiku 4.5<br/>chat completions"]
        Judge["Haiku 4.5<br/>structured extraction<br/>& verification"]
    end

    subgraph DB["Postgres"]
        TUsers[("users")]
        TAccounts[("accounts")]
        TOwners[("account_owners")]
        TTxs[("transactions")]
        TAudit[("audit_events")]
    end

    User --> UI
    User --> BankChat
    UI -->|HTTP/JSON| EpChat
    UI -->|HTTP/JSON| EpArgo
    UI -->|HTTP/JSON| EpAudit
    BankLlm -->|"HTTP/JSON<br/>{user_id, query, raw_response}"| EpGate
    Curl -->|HTTP/JSON| EpGate

    EpChat -->|sync| S1
    EpArgo -->|sync, starts at stage 0| Pipeline
    EpGate -->|sync, starts at stage 0,<br/>skips stage 1| Pipeline
    EpAudit -->|SELECT| TAudit
    EpHealth -.->|liveness| DB

    S0 -. reads .-> Loader
    S0 -->|"get_user · get_account_ids_for_user ·<br/>get_recent_payment_counterparties ·<br/>get_joint_account_co_owners"| DB

    S1 -->|"get_all_users · get_all_accounts_with_owners ·<br/>get_all_transactions"| DB
    S1 -->|chat prompt| Chat

    S2 -->|extraction prompt| Judge

    S3 -. reads bundle .-> S0

    S4 -->|"get_user_transactions(user_id)"| TTxs
    S4 -->|batched verification prompt| Judge

    S6 -->|INSERT| TAudit

    classDef store fill:#0b3a5f,stroke:#5fb0e8,color:#fff;
    classDef ext fill:#3d1f5e,stroke:#b388ff,color:#fff;
    classDef stage fill:#0d3c1f,stroke:#5fe39e,color:#fff;
    classDef gateway fill:#5a2a00,stroke:#ff9a3c,color:#fff;
    class TUsers,TAccounts,TOwners,TTxs,TAudit,Toml store
    class Chat,Judge ext
    class S0,S1,S2,S3,S4,S5,S6 stage
    class EpHealth,EpChat,EpArgo,EpAudit gateway
```

**Reading the diagram.** Two entry points share the same pipeline. The production path is `POST /argo/gate`: the bank's chat orchestrator owns stage 1 (the LLM call), then hands the `raw_response` to Argo so only stages 0 + 2–6 run. The demo path `POST /chat/argo` adds stage 1 internally — Argo dumps the bundled customer dataset to the LLM and gates the result, so the split-screen UI can show naive-vs-Argo from one round-trip. Both paths return the same `ArgoChatResponse` shape.

The pipeline runs six stages in order. The bundle resolver (stage 0) is the only piece that consults the policy file directly; it converts the TOML rules into per-user `owned_subjects` + `counterparty_visible` sets via four SQL helpers and caches the result for 30 seconds. Two LLM round-trips happen per request: one for the naive chat (stage 1) and one batched call for both extraction (stage 2) and verification (stage 4) — though the verification call is skipped when no claim needs source-checking. Everything else (entitlement check, rewriter, audit write) is pure Python plus one INSERT.

## Quick start

### Full demo (Docker)

```bash
cp .env.example .env   # add your Anthropic API key
make demo              # Postgres + gateway
```

Open **http://localhost:8000/ui** — the split-screen demo: a naive LLM
baseline (no gating) beside Argo's gated output, with the per-claim audit
trail underneath. Seven scripted demo queries are one click away.

### Local development

```bash
cp .env.example .env   # add your Anthropic API key
make install           # dependencies (incl. dev)
make run               # Postgres + gateway with hot reload
make help              # every target
```

### Testing

The suite runs with **no API key, no database, and no network** — every
pluggable seam has a test double (`FakeLlmClient`, `InMemoryAuditWriter`,
`SeedDerivedAdapter`, `SeedTransactionSource`), so the full gating pipeline
is exercised offline.

```bash
make test      # offline suite; live-DB tests skip
make ci        # lint + policy validation + tests, as CI runs them
```

Tests marked `db` exercise the production data path — `DbDerivedAdapter`,
`argo/db/queries.py`, `PostgresAuditWriter` — and need a real Postgres:

```bash
make db-up
make test-db
```

CI (`.github/workflows/ci.yml`) runs lint + policy validation, the offline
suite on Python 3.11/3.12/3.13, the live-Postgres suite against a service
container, and a Docker build with a compose health check.

See [CONTRIBUTING.md](CONTRIBUTING.md) to start contributing.

## API

| Endpoint | Purpose |
|---|---|
| **`POST /argo/gate`** | **Production integration point.** Your chat orchestrator owns the LLM call; Argo gates the response. Takes `{user_id, query, raw_response, chat_model?, chat_latency_ms?}` → returns `ArgoChatResponse` with the gated `final_response` and per-claim `claim_audit`. |
| `POST /chat/argo` | Demo convenience. Argo owns the LLM call too, so the split-screen UI can render both naive and gated output from one round-trip. Wraps `POST /argo/gate`. |
| `POST /chat` | Naive baseline only — full data dump to the LLM, no gating. The "before" half of the demo. |
| `GET /audit/recent` | Recent audit events for the demo audit panel. |
| `GET /health` | Liveness probe. |

### Integration patterns

**`/argo/gate` is the API real banks use.** The bank's existing chat
service builds the LLM context (with whatever retrieval / RAG / tool-
calling logic it already has), calls the LLM, and hands the response to
Argo for gating. Argo does not own the LLM call in this path.

```python
# Inside the bank's existing chat service
llm_response = bank.llm.complete(system=…, user=user_query)

argo_result = httpx.post("http://argo.internal:8000/argo/gate", json={
    "user_id":     verified_user_id,    # from your auth proxy
    "query":       user_query,
    "raw_response": llm_response.text,
    "chat_model":  llm_response.model,  # optional, for audit
}).json()

return argo_result["final_response"]    # gated text shown to customer
```

**`/chat/argo` is the demo convenience wrapper.** It calls `argo.naive`
to produce a raw_response from Argo's own Postgres, then delegates to
the same `gate_response()` internals as `/argo/gate`. Useful for the
split-screen UI and for evaluating Argo against the bundled demo data
without standing up your own chat orchestrator.

Both endpoints return the same `ArgoChatResponse` shape; audit semantics
are identical.

## How it works

The gated path runs six stages (`argo/pipeline.py`):

1. **Naive chat** (`argo/naive.py`) — the LLM answers with full data in context.
2. **Claim extraction** (`argo/judge.py`) — a Haiku judge breaks the response into discrete claims, each with a `subject`, `type`, `role`, and `source_span`.
3. **Entitlement check** (`argo/entitlements.py`) — each claim is scored against the asking user's bundle: `ALLOW | BLOCK | NEEDS_SOURCE_CHECK`.
4. **Source-span verification** (`argo/verifier.py`) — transaction claims are checked against the user's actual data; hallucinated or misattributed claims become `REDACT`.
5. **Rewrite** (`argo/rewriter.py`) — disallowed spans are redacted; an over-redacted response is swapped for a refusal.
6. **Audit** (`argo/db/audit.py`) — every claim, verdict, and reason is persisted.

### Request lifecycle

End-to-end sequence of a `POST /chat/argo` call (the demo path, which exercises every stage). The production `POST /argo/gate` path is identical except **stage 1 is skipped** — the bank's own chat orchestrator has already produced the `raw_response`, so the gateway enters at stage 2 with the response in hand.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Browser UI
    participant GW as FastAPI Gateway<br/>argo/main.py
    participant P as Pipeline<br/>argo/pipeline.py
    participant A as DbDerivedAdapter
    participant POL as POLICY<br/>banking.toml
    participant DB as Postgres
    participant N as naive_chat<br/>argo/naive.py
    participant CHAT as Anthropic Haiku<br/>(chat)
    participant EX as extract_claims_raw<br/>argo/judge.py
    participant J as Anthropic Haiku<br/>(judge)
    participant C as check_claim<br/>argo/entitlements.py
    participant V as resolve_verdicts<br/>argo/verifier.py
    participant RW as rewrite_response<br/>argo/rewriter.py
    participant AUD as write_audit_event<br/>argo/db/audit.py

    User->>UI: enters query
    UI->>+GW: POST /chat/argo {user_id, query}
    GW->>+P: run_argo_pipeline(user_id, query)

    rect rgba(50,120,80,0.18)
        Note over P,DB: Stage 0 — Resolve entitlement bundle
        P->>+A: get_bundle(user_id)
        alt cache hit (entry < 30s old)
            A-->>P: cached EntitlementBundle
        else cache miss
            A->>+DB: get_user(user_id)
            alt user does not exist
                DB-->>A: None
                A-->>P: raise UnknownUserError
                P-->>GW: HTTP 400
                GW-->>UI: {detail: "unknown user_id"}
            else exists
                DB-->>-A: user row
                A->>+DB: get_account_ids_for_user(user_id)
                DB-->>-A: owned account_ids
                A->>POL: read counterparty_rules
                loop for each rule in POLICY.counterparty_rules
                    alt rule.type == recent_payment
                        A->>+DB: get_recent_payment_counterparties(uid, lookback_days)
                        DB-->>-A: cp user_ids within window
                    else rule.type == joint_account_co_owner
                        A->>+DB: get_joint_account_co_owners(uid)
                        DB-->>-A: co-owner user_ids
                    end
                end
                A->>POL: read counterparty_fields whitelist
                A-->>-P: EntitlementBundle (stored in cache, TTL 30s)
            end
        end
    end

    rect rgba(80,120,200,0.18)
        Note over P,CHAT: Stage 1 — Naive chat (full data dump baseline)
        P->>+N: naive_chat(user_id, query)
        N->>+DB: get_all_users / accounts_with_owners / all_transactions
        DB-->>-N: full dataset
        N->>+CHAT: chat completion(system + user prompt)
        CHAT-->>-N: raw_response
        N-->>-P: (raw_response, model, chat_ms)
    end

    rect rgba(180,120,80,0.18)
        Note over P,J: Stage 2 — Extract claims
        P->>+EX: extract_claims_raw(raw_response, user_id)
        EX->>+J: extraction prompt
        J-->>-EX: JSON {claims: [...]}
        alt parse failure
            EX-->>P: None (fail-closed)
            P->>AUD: audit "extractor parse failure → REFUSAL"
            P-->>GW: ArgoChatResponse(final=REFUSAL, whole_blocked=true)
        else parsed ok
            EX-->>-P: ParsedClaims
        end
    end

    rect rgba(120,80,160,0.18)
        Note over P,C: Stage 3 — Entitlement check (synchronous, no LLM)
        loop for each claim
            P->>+C: check_claim(claim, bundle)
            C-->>-P: VerdictResult<br/>(ALLOW · BLOCK · NEEDS_SOURCE_CHECK)
        end
    end

    rect rgba(80,180,180,0.18)
        Note over P,J: Stage 4 — Source-span verify (resolves NEEDS_SOURCE_CHECK)
        P->>+V: resolve_verdicts(claims_with_verdicts, user_id)
        alt no NEEDS_SOURCE_CHECK claims
            V-->>P: unchanged (no LLM call)
        else has claims to verify
            V->>+DB: get_user_transactions(user_id)
            DB-->>-V: user's tx history
            V->>+J: batched verification prompt
            J-->>-V: per-claim {match, matched_tx_id, reason}
            V-->>-P: resolved (NEEDS_SOURCE_CHECK → ALLOW or REDACT)
        end
    end

    rect rgba(200,90,90,0.18)
        Note over P,RW: Stage 5 — Rewrite
        P->>+RW: rewrite_response(raw_response, resolved)
        alt too much redacted (whole-block threshold)
            RW-->>P: REFUSAL_TEXT (whole_blocked=true)
        else partial redactions
            RW-->>-P: final_response with [redacted] spans
        end
    end

    rect rgba(100,100,180,0.18)
        Note over P,DB: Stage 6 — Audit
        P->>+AUD: write_audit_event(user_id, query, raw, final, claims, verdicts, timings)
        AUD->>+DB: INSERT INTO audit_events
        DB-->>-AUD: audit_id
        AUD-->>-P: audit_id
    end

    P-->>-GW: ArgoChatResponse {raw, final, claim_audit, timings, audit_id}
    GW-->>-UI: JSON 200
    UI->>User: split-screen render (naive vs Argo + audit table)
```

**Reading the diagram.** Every coloured band is one of the six pipeline stages. The two LLM endpoints (chat vs judge) are drawn separately even though both call Haiku 4.5 — different system prompts, different roles. Two fail-closed branches are explicit: an unknown `user_id` short-circuits to HTTP 400 at stage 0, and an extractor parse failure short-circuits to a generic refusal with an audit row recording the failure. The verifier (stage 4) is the only stage that may skip its LLM round trip entirely — if no claim needed source-checking, it returns the input unchanged.

## Documentation

| Doc | When to read |
|---|---|
| [SECURITY.md](SECURITY.md) | Before deploying. Threat model, trust boundaries, identity contract, known limitations. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | When you're ready to run it past `docker-compose`. Every env var, Kubernetes shape, common errors. |
| [ADAPTERS.md](ADAPTERS.md) | When the default Postgres / Anthropic / Postgres-audit stack isn't your stack. Worked Okta + Bedrock + Splunk examples. |
| [POLICY.md](POLICY.md) | When you're authoring `banking.toml` for your bank. Full reference, validation errors, versioning advice. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Before your first PR. Setup, workflow, what a good change looks like. |
| [AGENTS.md](AGENTS.md) | Working in this repo — repo map, invariants, conventions. Written for coding agents; useful to humans too. |

## Project layout

```
argo/               # gateway package — FastAPI app + the six pipeline stages
argo/db/            # Postgres access, schema, seed data, audit log
argo/policy/        # GRC-reviewable policy file + loader
argo/clock.py       # single reference instant for time-windowed rules
argo/plugins.py     # plugin loader for the five pluggable Protocols
static/             # demo UI (single-file vanilla HTML/CSS/JS)
tests/              # pytest suite — offline by default, `db`-marked tests need Postgres
scripts/            # eval + validation harnesses (call the live LLM)
eval/               # labeled claim set + baseline characterization
Makefile            # every dev command; `make help` to list them
.github/workflows/  # CI: lint, tests on 3.11–3.13, live Postgres, Docker build
docker-compose.yml  # Postgres + gateway
Dockerfile          # gateway image
```

## License

Apache 2.0. See [LICENSE](LICENSE).
