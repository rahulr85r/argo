# Security

This document is the threat model for Argo and the contract Argo expects from
its environment. If you are deploying Argo inside a bank, your security team
should be able to read this in one sitting and decide whether to approve it.

If you find a vulnerability, see [Reporting vulnerabilities](#reporting-vulnerabilities)
at the bottom — please do not open a public issue.

---

## 1. What Argo defends against

Argo solves exactly one problem: **OWASP LLM Top 10 #2 — Sensitive Information
Disclosure** at output synthesis time.

Concretely: given a user's natural-language query and an LLM-generated answer
that was produced with too much data in context, Argo decides what survives
to the user. It catches three failure modes that upstream gates (IAM, MCP
tool gates, RBAC) structurally cannot:

1. **Counterparty data on legitimately-returned rows.** Your tool call
   `get_transactions(user=A)` correctly returns A's row. That row mentions
   counterparty B's name and amount. Whether the model paraphrases over the
   field-scope rule and reveals B's account number (which also happened to be
   on the row) is a generation-time decision. Argo's claim extractor + policy
   check decides per claim.

2. **Hallucinations.** The source-span verifier rejects any claim whose
   support is not present in the retrieved context. This is **policy-
   independent** — it answers "is this claim grounded?", not "is this claim
   allowed?" — so fabricated facts about any entity fail closed regardless of
   entitlement configuration.

3. **No output-level audit.** Your IAM logs say "user A fetched their
   transactions." They do not say "the model emitted B's balance and the gate
   redacted it for reason X." Argo's audit log is at the claim level.

## 2. What Argo does NOT defend against

This list is explicit because mis-scoping is the most common way security
tools fail in audit. Argo does **none** of the following:

- **Authentication.** Argo does not validate JWTs, manage sessions, or check
  passwords. It trusts an upstream-set header (see §4). If that header is
  forgeable in your environment, Argo is not a control.
- **Authorization at the tool / data layer.** If your LLM agent can call
  `get_transactions(user=Z)` when the asking user is A, that is an IAM/MCP
  problem, not an Argo one. Argo runs after the call returned.
- **Prompt injection defense for the chat model.** Argo extracts claims from
  whatever the chat model emitted. A successful prompt injection that makes
  the model emit no claims (e.g., "just return the digit 7") will pass
  through cleanly because there is nothing to gate.
- **Output content moderation.** Toxicity, bias, off-topic responses,
  copyright, PII patterns in *the user's own data*. Not Argo's job.
- **Rate limiting / DDoS protection.** Use your API gateway.
- **Secrets exfiltration via tool calls.** Tool-call gating is out of scope.
- **Model jailbreaks that bypass the chat model's own safety.** Argo gates
  output, but if the model emits content that wasn't asked for but is also
  about the user's own data, Argo allows it (it's the user's data).
- **Multi-modal output** (images, audio, generated documents). Text only.
- **Streaming responses.** The Phase-0 pipeline is fully buffered; the chat
  model must finish before Argo can gate. If you ship a streaming UX, you
  must either wait for the full response or build a streaming gating layer.

If any of the above are part of your threat model, you need other controls
*in addition to* Argo, not in place of it.

## 3. Trust boundaries

Argo has three trust boundaries:

```
┌────────────────────────────────────────────────────────────────────────┐
│  1. Bank's auth proxy (Kong, NGINX, Envoy, AWS API Gateway, etc.)      │
│     - Validates the customer's JWT                                     │
│     - Sets X-Argo-User-Id header with the verified user_id             │
│     - Strips any incoming X-Argo-User-Id from the request              │
│  ↓                                                                     │
│  2. Argo gateway                                                       │
│     - Trusts X-Argo-User-Id at face value                              │
│     - Runs the 6-stage pipeline                                        │
│     - Returns gated response + audit_id                                │
│  ↓                                                                     │
│  3a. Postgres                       3b. LLM provider                   │
│     - Owns: users, accounts,            - Anthropic / Bedrock /        │
│       transactions, audit_events          self-hosted (via LiteLLM)    │
│     - Argo holds DB credentials         - Argo holds API key /         │
│       in env vars (no secrets             IAM role                     │
│       in code)                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

**Each boundary is a deploy-time obligation:**

- The **auth proxy** is *the* authentication control. If it can be bypassed,
  Argo's `user_id` becomes attacker-controlled and every gate decision is
  compromised. This is not theoretical — see §6 (Known limitations).
- The **Argo gateway** must be reachable only from the auth proxy. Run Argo
  in a private subnet / mesh-internal network; do not expose its ports
  publicly.
- The **data stores** must not be shared with untrusted workloads. The DB
  credentials and the LLM API key are the keys to every customer's data.

## 4. Identity contract

Argo reads the asking user's identity from an HTTP header configured via
`USER_ID_HEADER` (default `X-Argo-User-Id`). The pipeline trusts this value
without further verification.

**Your auth proxy MUST:**

1. Verify the customer's session / JWT / mTLS cert before forwarding.
2. Set `X-Argo-User-Id` to the verified, canonical user identifier.
3. **Strip any inbound `X-Argo-User-Id`** from the original request so a
   malicious client cannot smuggle one in.

A minimal NGINX example:

```nginx
location /argo/ {
    # Drop any header the client tried to send
    proxy_set_header X-Argo-User-Id "";
    # Re-set from the verified JWT subject
    proxy_set_header X-Argo-User-Id $jwt_claim_sub;
    proxy_pass http://argo-gateway:8000/;
}
```

A minimal Envoy example uses a `header_to_metadata` filter plus a
`header_mutation` filter — equivalent shape.

**Why this design?** Argo runs inside the bank. Your auth infrastructure is
already audited, already integrated with your IDP, already monitored. Re-
implementing it inside Argo would mean re-auditing it, and would also force
you to keep two copies of your session/token logic in sync. The trade is:
Argo is dead-simple to integrate (one header) at the cost of being useless
without an auth proxy in front. That is the right trade for a bank.

## 5. Data flow & retention

Per `POST /chat/argo` request, Argo handles:

| Data | Source | Lifetime |
|---|---|---|
| `user_id` | header from auth proxy | request-scoped |
| `query` (user's prompt) | request body | audit-event-scoped |
| LLM-context data | Postgres tables | request-scoped (sent to LLM, see §5.1) |
| `raw_response` (naive output) | LLM provider | audit-event-scoped |
| `final_response` (gated output) | rewriter | audit-event-scoped + returned to client |
| Per-claim verdicts + reasons | policy engine + verifier | audit-event-scoped |
| Latency timings | pipeline | audit-event-scoped |

**The audit event persists until you decide.** The default Postgres writer
INSERTs into `audit_events` and never deletes. You are responsible for the
retention policy — see §7.

### 5.1 What Argo sends to the LLM provider

Argo makes up to three LLM calls per `POST /chat/argo`:

- **Chat call (naive):** the user's query + the bank's full customer dataset
  as system context (default `argo/naive.py`). **This is the most data-rich
  call.** If your LLM provider is external (Anthropic SaaS, OpenAI), every
  customer's data crosses your trust boundary on every chat. For regulated
  banks, this is usually unacceptable — point `LLM_CLIENT` at a self-hosted
  or in-VPC model (e.g., AWS Bedrock in the bank's VPC).
- **Judge call (extractor):** the *naive response only* + an extraction
  prompt. No customer data beyond what the model already emitted.
- **Judge call (verifier):** the claims needing source-check + the asking
  user's transaction list. Single-user scope.

If you cannot run a model in-VPC, your only safe option for production
banking deployment is to drastically narrow the naive call's context (e.g.,
only the asking user's data, no full directory) or to disable the naive
baseline path entirely.

### 5.2 What Argo logs (not audits)

The application logs (stdout) currently include request metadata and
exception tracebacks. They do **not** include the LLM context blob, the
user's query, or the response text. Configure your log shipper to scrub
anything Argo's `logging` output emits before it reaches a SIEM that is not
authorized for customer data.

## 6. Known limitations

Be aware of these before deploying. None of them are bugs — they are
trade-offs we made in Phase 0.

1. **In-process entitlement cache, 30s TTL.** A user whose entitlements
   change in your IDP will see the old bundle for up to 30 seconds. For
   slower changes (employment status, account closures), this is fine; for
   security-sensitive revocations (fraud alert, account freeze), 30 seconds
   is too long. Tune via `DbDerivedAdapter(ttl_seconds=…)` or write a custom
   adapter that subscribes to your IDP's revocation events.

2. **Audit writes are best-effort.** The default `PostgresAuditWriter`
   commits synchronously, but if the INSERT fails the pipeline still returns
   the gated response to the user. For regulated audit ("every gate
   decision must be persisted"), implement an `AuditWriter` that fans out
   to a durable queue (Kafka, SQS) before the response returns, or have
   `.write()` raise on persist failure.

3. **No request signing between gateway and DB / LLM.** Argo uses plain
   credentials. Standard practice: mTLS or service-mesh-issued identities.
   Argo neither helps nor hinders; configure at the deployment layer.

4. **The verifier uses an LLM.** The default `LlmVerifier` matches claims to
   transactions via a Haiku call. LLM matching has non-deterministic edge
   cases. For higher assurance, write a SQL-first verifier (deterministic
   match on amount/date/direction; fall back to LLM only on ambiguity).

5. **Fail-closed paths.** Two paths short-circuit to a generic refusal: an
   unknown `user_id` (HTTP 400) and an extractor parse failure (REFUSAL +
   audit row). Other errors (DB unavailable, LLM unavailable) raise 500.
   Plan your client behavior accordingly.

6. **Policy is loaded at startup.** Edits to `banking.toml` require a
   gateway restart. Hot-reload is intentionally not provided in Phase 0 —
   policy changes are change-control events, not runtime tweaks.

## 7. Operator obligations

If you deploy Argo in production, you are responsible for:

- **Auth proxy.** Per §4. Argo is not a control without it.
- **Network isolation.** Argo and Postgres must be unreachable from the
  public internet.
- **Secrets management.** `ANTHROPIC_API_KEY` and DB credentials in a
  secret store, not in `.env` files on disk. The gateway reads them from
  environment variables at startup; how they get there is your problem.
- **Audit retention.** Decide and document. The default writer never deletes.
- **Policy review cadence.** `banking.toml` is the GRC-reviewable artifact.
  Treat changes like any other policy change — review, approval, version
  control, change-window deployment.
- **Vulnerability monitoring.** Subscribe to GitHub security advisories on
  this repo and on the upstream dependencies (`uv.lock`).
- **Incident response.** If you suspect a leak, the `audit_events` table is
  the forensic record. Query by `user_id`, `ts`, and `whole_blocked = false
  AND redacted_chars = 0` to find responses that *passed unchanged*.

## 8. Supply chain

- All dependencies are pinned in `uv.lock`. Reproduce builds via `uv sync
  --frozen`.
- Container image: built from `Dockerfile` in the repo root. Inspect before
  using a pre-built image; we do not currently publish signed images.
- The Apache 2.0 license permits modification and redistribution — your
  forks and internal builds are within scope.

## Reporting vulnerabilities

If you find a vulnerability, please email the maintainer privately rather
than opening a public issue or PR. Include:

- A description of the vulnerability and the affected version (commit hash
  or release tag).
- A minimal reproduction.
- Your assessment of severity and exploitability.
- Whether you intend to disclose publicly, and on what timeline.

We aim to acknowledge within 5 business days. Coordinated disclosure on a
mutually agreed timeline is the default.

---

This document tracks the code; if you change a trust boundary, the
identity contract, or the data flow, update this file in the same PR.
