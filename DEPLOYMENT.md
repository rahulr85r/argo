# Deployment

This document takes you from `git clone` to a running, production-shaped
Argo deployment. If you are evaluating Argo locally, jump straight to
[1. Local evaluation](#1-local-evaluation). If you are deploying inside a
bank, read [2. Production architecture](#2-production-architecture) first,
because the local setup deliberately leaves out things you must add.

Throughout: anywhere you see `<angle-brackets>`, substitute your own value.

---

## Prerequisites

| Tool | Minimum | Notes |
|---|---|---|
| Docker + docker-compose | 24.x | Local evaluation only |
| Python | 3.11+ | Only if developing or running outside Docker |
| `uv` | 0.4+ | Python package manager (`brew install uv` or [docs](https://docs.astral.sh/uv/)) |
| Anthropic API key | — | For default LLM client; swap out via `LLM_CLIENT` |
| Postgres | 15+ | Bundled in docker-compose for local; bring your own for production |

You also need outbound HTTPS to `api.anthropic.com` (or wherever your
configured LLM lives). If your network blocks outbound, configure an
in-VPC LLM endpoint via `CHAT_MODEL` / `JUDGE_MODEL` — see §3.

---

## 1. Local evaluation

The fastest possible path. Two terminals, ~3 minutes.

```bash
git clone https://github.com/rahulr85r/argo.git
cd argo
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-…
docker-compose up -d --build
```

Wait for the gateway to be ready:

```bash
until curl -sf http://localhost:8000/health; do sleep 1; done
```

Open <http://localhost:8000/ui> in a browser. You will see the split-screen
demo. Pick user_a from the dropdown and run any of the 7 scripted queries.

To stop and wipe everything:

```bash
docker-compose down -v   # -v also removes the Postgres volume (seed re-runs on next up)
```

### What you just got

- Postgres container with 26 seeded users + 231 transactions
- FastAPI gateway on port 8000 with three endpoints (`/chat`, `/chat/argo`, `/audit/recent`)
- The default plugin stack: `DbDerivedAdapter`, `PostgresAuditWriter`,
  `LiteLlmClient` pointing at Anthropic Haiku 4.5, `LlmVerifier`
- No authentication. **Anyone on localhost can pose as any user.** Fine
  for evaluation, not for production.

If something didn't work, see [Common errors](#common-errors).

---

## 2. Production architecture

A bank-grade deployment has at least five components. Argo is one of them.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Customer's browser / mobile app                                        │
└───────────┬─────────────────────────────────────────────────────────────┘
            │ HTTPS (customer's JWT in Authorization header)
            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bank's auth proxy / API gateway                                        │
│  - Validates JWT, mTLS, etc.                                            │
│  - Strips incoming X-Argo-User-Id (security-critical, see SECURITY.md)  │
│  - Sets X-Argo-User-Id to the verified subject                          │
└───────────┬─────────────────────────────────────────────────────────────┘
            │ mTLS or service-mesh internal
            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Argo gateway (this repo, deployed as a container)                      │
│  - Reads X-Argo-User-Id, runs the 6-stage pipeline                      │
│  - Stateless: scale horizontally behind a load balancer                 │
└─────┬──────────────────────────┬──────────────────────────┬─────────────┘
      │                          │                          │
      ▼                          ▼                          ▼
┌──────────────┐         ┌──────────────────┐     ┌──────────────────────┐
│  Postgres    │         │  LLM endpoint    │     │  SIEM / audit sink   │
│  (bank's)    │         │  (in-VPC or      │     │  (Splunk / Datadog / │
│              │         │   external)      │     │   Kafka / S3)        │
└──────────────┘         └──────────────────┘     └──────────────────────┘
```

You provide #1, #2, #4 and (usually) #5. Argo is #3 plus the default writer
for #5 if you don't have one yet.

### 2.1 What you'll change vs. the local setup

| Layer | Local | Production |
|---|---|---|
| Auth | none | auth proxy in front, `USER_ID_HEADER` set |
| Postgres | container in docker-compose | bank-managed (RDS, CloudSQL, on-prem) |
| LLM | Anthropic SaaS | in-VPC (Bedrock with private endpoints, self-hosted, etc.) |
| Audit | Postgres `audit_events` | SIEM via a custom `AuditWriter` |
| Secrets | `.env` file | secret manager (Vault, AWS Secrets Manager, K8s Secrets) |
| Policy | bundled `argo/policy/banking.toml` | mounted from your config repo |
| Deployment | docker-compose | Kubernetes, ECS, or your usual platform |
| TLS | none | terminated at auth proxy |

Sections 3–6 walk through each.

---

## 3. Configuration reference

Every knob is an environment variable. Argo reads them at startup; restart
the gateway to pick up changes.

### Required

| Variable | Example | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-…` | Default LLM provider key. Not required if you replace `LLM_CLIENT`. |
| `POSTGRES_HOST` | `db.internal` | Postgres host the gateway connects to. |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `argo` | |
| `POSTGRES_USER` | `argo` | |
| `POSTGRES_PASSWORD` | `<secret>` | Read this from a secret manager, not a file. |

### Identity contract

| Variable | Default | What it does |
|---|---|---|
| `USER_ID_HEADER` | `X-Argo-User-Id` | The HTTP header Argo reads to identify the asking user. Your auth proxy MUST set this and MUST strip any inbound value. See [SECURITY.md §4](SECURITY.md#4-identity-contract). |

### LLM routing

| Variable | Default | What it does |
|---|---|---|
| `CHAT_MODEL` | `anthropic/claude-haiku-4-5` | LiteLLM model string for the customer-facing chat. |
| `JUDGE_MODEL` | `anthropic/claude-haiku-4-5` | LiteLLM model string for extractor + verifier. |

Examples for switching providers (LiteLLM understands the prefix):

```bash
# AWS Bedrock (recommended for regulated banks — stays in your VPC)
CHAT_MODEL=bedrock/anthropic.claude-haiku-4-5-20251001-v1:0
JUDGE_MODEL=bedrock/anthropic.claude-haiku-4-5-20251001-v1:0
# Credentials come from the standard AWS chain (IRSA on EKS, instance profile on EC2)

# Azure OpenAI
CHAT_MODEL=azure/<deployment-name>
JUDGE_MODEL=azure/<deployment-name>

# Self-hosted vLLM / OpenAI-compatible endpoint
CHAT_MODEL=openai/<model-name>
OPENAI_API_BASE=https://llm.internal/v1
OPENAI_API_KEY=<service-token>
```

If LiteLLM can't reach your provider the way you want it to, write a custom
`LlmClient` (see [ADAPTERS.md](ADAPTERS.md)) — that's exactly what it's for.

### Policy

| Variable | Default | What it does |
|---|---|---|
| `POLICY_PATH` | `""` (bundled file) | Absolute path to your policy TOML. In production, mount your bank's policy file and point this at it. See [POLICY.md](POLICY.md). |
| `REFERENCE_TIME` | `""` (wall clock) | Reference instant for time-windowed rules like `recent_payment`. **Leave empty in production.** `seed` pins to the bundled demo dataset; an ISO-8601 timestamp pins to that instant (useful for replaying a past entitlement decision during an audit). |

> **Do not pin `REFERENCE_TIME` in production.** A pinned reference freezes
> the counterparty graph: relationships that should age out of the
> `recent_payment` window never do, so entitlements only ever widen. The
> bundled demo pins to `seed` because its transactions are fixed dates that
> would otherwise decay out of their own window — see [POLICY.md](POLICY.md).

### Plugin selection

Each Protocol's implementation is selected by a `module:Class` string. To
override, install the plugin package and set the env var. See
[ADAPTERS.md](ADAPTERS.md) for what each Protocol does.

| Variable | Default |
|---|---|
| `ENTITLEMENT_ADAPTER` | `argo.entitlements:DbDerivedAdapter` |
| `AUDIT_WRITER` | `argo.db.audit:PostgresAuditWriter` |
| `LLM_CLIENT` | `argo.llm:LiteLlmClient` |
| `VERIFIER` | `argo.verifier:LlmVerifier` |

Example with a custom Okta adapter and Splunk audit writer:

```bash
ENTITLEMENT_ADAPTER=mybank.argo_plugins:OktaAdapter
AUDIT_WRITER=mybank.argo_plugins:SplunkHecWriter
```

(Both packages must be installed in the same Python environment as Argo.)

---

## 4. Kubernetes deployment

The shape below is intentionally minimal — adapt it to whatever your
platform team's standard manifest looks like.

### 4.1 Image

The repo's `Dockerfile` builds the gateway. Build and push to your
registry:

```bash
docker build -t <registry>/argo:<tag> .
docker push <registry>/argo:<tag>
```

For reproducible builds inside your CI, use `uv sync --frozen` (already in
the Dockerfile).

### 4.2 Minimum manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: argo-gateway
spec:
  replicas: 3
  selector:
    matchLabels: { app: argo-gateway }
  template:
    metadata:
      labels: { app: argo-gateway }
    spec:
      containers:
        - name: gateway
          image: <registry>/argo:<tag>
          ports:
            - containerPort: 8000
          env:
            - name: POSTGRES_HOST
              value: argo-postgres.svc.cluster.local
            - name: POSTGRES_USER
              valueFrom: { secretKeyRef: { name: argo-db, key: user } }
            - name: POSTGRES_PASSWORD
              valueFrom: { secretKeyRef: { name: argo-db, key: password } }
            - name: ANTHROPIC_API_KEY
              valueFrom: { secretKeyRef: { name: argo-llm, key: api_key } }
            - name: POLICY_PATH
              value: /etc/argo/policy.toml
          volumeMounts:
            - { name: policy, mountPath: /etc/argo, readOnly: true }
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests: { cpu: 250m, memory: 512Mi }
            limits:   { cpu: 1,    memory: 1Gi }
      volumes:
        - name: policy
          configMap:
            name: argo-policy   # holds your bank's banking.toml
---
apiVersion: v1
kind: Service
metadata: { name: argo-gateway }
spec:
  selector: { app: argo-gateway }
  ports: [{ port: 8000, targetPort: 8000 }]
```

### 4.3 Important deployment notes

- **Stateless gateway.** Three replicas above; scale horizontally as needed.
  Each replica has its own 30-second entitlement cache; cache misses cost
  two SQL queries. For larger deployments, write a Redis-backed adapter
  so all replicas share state.
- **Pod identity for the LLM.** If you're on Bedrock, use IRSA / Workload
  Identity instead of a static API key — drop the `ANTHROPIC_API_KEY`
  secret and rely on the AWS SDK's default credential chain that LiteLLM
  uses.
- **Auth proxy is upstream.** This manifest does not include one. Put your
  cluster's standard ingress / service mesh in front (Istio, Linkerd,
  Kong, etc.) and configure it to set `X-Argo-User-Id` per
  [SECURITY.md §4](SECURITY.md#4-identity-contract).
- **No `Service` exposed externally.** The Service above is `ClusterIP` by
  default — only reachable inside the cluster.

---

## 5. Database setup

### 5.1 Schema

Argo creates its schema on first startup (`argo/db/schema.sql`, applied
idempotently by `argo/db/bootstrap.py`). No separate migration step on a
fresh database.

For an existing Postgres, run the bootstrap once before pointing the
gateway at it:

```bash
psql "$DATABASE_URL" -f argo/db/schema.sql
```

### 5.2 Tables Argo owns

| Table | Purpose | Argo writes? | Argo reads? |
|---|---|---|---|
| `users` | Customer roster | seed only | every chat |
| `accounts` | Customer accounts | seed only | every naive chat + balance render |
| `account_owners` | Who owns what | seed only | every bundle build |
| `transactions` | Tx ledger | seed only | naive chat + verifier |
| `audit_events` | Per-chat audit trail | every chat | `/audit/recent` |

For a production deployment, **you** own these tables. Argo's seed is for
the demo; replace `argo/db/seed.py` with your bank's own data loaders, or
disable it entirely by ensuring `users` is non-empty before first startup
(the seed is idempotent — it short-circuits on a non-empty table).

### 5.3 Schema migrations

There is intentionally no migration framework in Phase 0 (no Alembic,
Liquibase, etc.). When a future Argo release changes the schema, the
release notes will include the `ALTER TABLE` statements. For production
deployments, wrap those in your standard migration tool.

### 5.4 Connection pooling

The current connection helper opens a fresh psycopg connection per query.
For higher throughput, run a PgBouncer (or RDS Proxy) in front and point
`POSTGRES_HOST` at it. The gateway code does not need to change.

---

## 6. Observability

Argo emits to stdout. Your platform's log shipper picks it up. There are
no built-in dashboards — that is your monitoring stack's job.

### 6.1 What to scrape

- **Process metrics:** standard container / pod metrics.
- **HTTP metrics:** standard ingress / mesh metrics on the gateway pod.
- **Application metrics:** *not currently exposed.* A `/metrics` Prometheus
  endpoint is on the roadmap; in the meantime, derive metrics from
  request logs.
- **Audit events:** the `audit_events` table is the authoritative record
  of every gate decision. For SIEM-shipping, write a custom `AuditWriter`
  (see ADAPTERS.md) so events flow to Splunk / Datadog / Kafka directly
  rather than via Postgres polling.

### 6.2 What to alert on

| Signal | Why it matters |
|---|---|
| 5xx rate > 1% | gateway or LLM provider degraded |
| p95 `/chat/argo` latency > 10s | LLM provider degraded |
| `audit_events.whole_blocked = true` rate spikes for one user | possible compromise or LLM regression |
| `audit_events` INSERT failures | audit trail at risk; investigate immediately |
| `ENTITLEMENT_ADAPTER` raises `UnknownUserError` for known users | IDP / identity-proxy mismatch |

---

## 7. Upgrading

1. Read the release notes for breaking changes (schema, env-var renames,
   Protocol-interface changes).
2. Test in staging against your production-shaped data.
3. If the release includes a schema change, run the migration during a
   maintenance window before deploying the new image.
4. Roll the gateway pods. Stateless — no draining required beyond your
   platform's default rolling-deploy behavior.
5. Verify `/health`, then verify a known query through your auth proxy.

Argo follows semantic versioning. Breaking changes to a Protocol interface
or the policy file format are major-version bumps.

---

## Common errors

### `litellm.exceptions.RateLimitError`

You hit the LLM provider's per-minute token limit. Default Haiku tier is
50K input tokens/min. Solutions:

- Pace your testing (one `/chat/argo` per ~30s at default config).
- Upgrade the LLM provider tier.
- Switch to a self-hosted model via `CHAT_MODEL` / `JUDGE_MODEL`.

### `psycopg.OperationalError: could not connect to server`

Check `POSTGRES_HOST` is reachable from the gateway pod. In Kubernetes,
this usually means the DB Service name is wrong or the network policy
blocks the connection.

### `UnknownUserError: <user_id>`

The header value Argo received is not in the `users` table. Either:

- Your auth proxy is forwarding the wrong identifier (check what JWT
  claim you're using — is it the customer ID or a session ID?).
- The user genuinely doesn't exist in your customer master and `users`
  table is stale.

### `argo.policy` validation error at startup

Your `banking.toml` references an unknown rule type or claim type. The
error message names the offender. See [POLICY.md](POLICY.md) for the
valid set.

### Gateway returns 401 / 403 for everything

You haven't deployed the auth proxy. Argo itself does not return 401
(it has no concept of authentication); these come from your proxy.

### Pipeline is slow (>10s per `/chat/argo`)

Default config makes up to 3 LLM round trips per request. Naive chat is
~2-3s; extractor is ~2s; verifier is ~2s (skipped if no claim needs
source-check). To reduce:

- Smaller LLM context (limit what `naive.py` sends).
- Faster model on the judge path (smaller Haiku, custom fine-tune).
- Parallelize where possible (custom pipeline).

### `RuntimeError: plugin spec '…' could not import module '…'`

The package providing your custom adapter isn't installed in the same
Python environment as Argo. `pip install <your-plugin-package>` inside
the gateway image.

---

If you hit something not covered here, open an issue with the exact error
and the values of every env var (redacting secrets). Most "doesn't work"
reports trace back to one of: missing env var, wrong policy path,
auth-proxy misconfig.
