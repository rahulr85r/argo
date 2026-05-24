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

## Where Argo fits

Existing access controls — IAM, RBAC, MCP tool gates — operate at **tool-call and data-fetch time**: "can the LLM call `get_transactions(user=A)`?" Argo operates at **output synthesis time**: "given a legitimate tool response, does the generated text respect field-level scope?"

Three failure modes upstream gates can't catch on their own:

1. **Counterparty data on legitimately-returned rows.** A user's own transaction row correctly includes the counterparty's name and amount. Whether the model paraphrases over field scope — e.g., reveals a counterparty's account number that happened to be on the row — is a generation-time decision. No upstream gate can predict what the model will say about data it was correctly given.
2. **Hallucinations.** The source-span verifier rejects any claim whose support isn't in retrieved context. This check is **policy-independent** — it answers "is this claim grounded?", not "is this claim allowed?" — so fabricated facts about *any* entity fail closed, regardless of entitlement configuration.
3. **Output-level audit.** IAM logs say "user A fetched their transactions." They don't say "model emitted counterparty's balance, redacted, reason=`field-not-whitelisted`." Argo's audit log is at the claim level.

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
        EpArgo["POST /chat/argo<br/>(gated pipeline)"]
        EpAudit["GET /audit/recent"]
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
    User --> Curl
    UI -->|HTTP/JSON| EpChat
    UI -->|HTTP/JSON| EpArgo
    UI -->|HTTP/JSON| EpAudit
    Curl -->|HTTP/JSON| EpArgo

    EpChat -->|sync| S1
    EpArgo -->|sync| Pipeline
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

**Reading the diagram.** A request enters the FastAPI gateway at `POST /chat/argo` and the pipeline runs six stages in order. The bundle resolver (stage 0) is the only piece that consults the policy file directly; it converts the TOML rules into per-user `owned_subjects` + `counterparty_visible` sets via four SQL helpers and caches the result for 30 seconds. Two LLM round-trips happen per request: one for the naive chat (stage 1) and one batched call for both extraction (stage 2) and verification (stage 4) — though the verification call is skipped when no claim needs source-checking. Everything else (entitlement check, rewriter, audit write) is pure Python plus one INSERT.

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

### Request lifecycle

End-to-end sequence of a `POST /chat/argo` call, including every external round trip and the cache / fail-closed branches:

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
