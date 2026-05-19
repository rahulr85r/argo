# Argo — Wedge Doc

**Tagline:** The open-source runtime gate that stops your LLM from telling the wrong user the right answer.

**Version:** 0.2 (Wedge)
**Date:** 2026-05-17
**Status:** Problem locked. Pre-build.
**Supersedes:** PRD v0.1 (three-failure-mode framing, now retired).

---

## 1. The one problem we solve

When a user interacts with an LLM-powered application, the model can reach — through context, tool calls, or retrieval — information that belongs to a *different* user. It surfaces that information helpfully. The data was reachable; it just should not have come out of the bot's mouth to *this* user.

> Example: User A asks a private-banking chatbot about a recent transfer. The model has read access to the full account graph. The response includes a balance, a transaction, or a name that belongs to User B. No malicious actor. No prompt injection. The model did what it was asked to do, well, and that is the problem.

This failure mode has a name in the OWASP LLM Top 10 (2025): **Sensitive Information Disclosure, ranked #2**. It is the single thing every regulated chatbot deployment quietly fears. It has no general solution today.

That is the entire problem statement. Argo solves exactly this. Nothing else.

---

## 2. Why no existing tool solves it

Every open-source guardrails project filters on **content categories** (is this PII? toxic? a URL?). None filters on **identity entitlements** (is *this user* allowed to see *this content*?). Verified by reading the repos:

| Project | Output filtering | Identity-aware? |
|---|---|---|
| LLM Guard (Protect AI) | 21 scanners — BanCode, Bias, Deanonymize, MaliciousURLs, Sensitive, Toxicity, etc. | No |
| NeMo Guardrails (NVIDIA) | Input / dialog / retrieval / execution / output rails | No — docs say "build a custom action" |
| Guardrails AI | Schema + content-category validators (RegexMatch, ToxicLanguage, CompetitorCheck) | No — docs say "build a custom validator" |
| OpenGuardrails (arXiv Oct 2025) | Safety + manipulation + generic PII NER | No — paper: "fundamentally a safety system, not an access-control system" |

Commercial side: **PlainID** markets "response-layer authorization" but discloses no per-claim entitlement mechanism. **Axiomatics** filters at retrieval, not output. **Lakera, Nightfall, Bedrock Guardrails** are pattern matchers. None unify output-time + per-claim + per-user entitlements + audit-grade trail in a single product.

The gap is real, not a search miss.

---

## 3. What Argo is

A runtime gateway that sits between the host application and the LLM. For every response the LLM generates, Argo does four things:

1. **Extract claims.** A small-LLM judge breaks the response into discrete factual claims, each tied to a source span.
2. **Check entitlements.** For each claim, evaluate `(user, claim) → allow | redact | block` against the user's entitlement bundle.
3. **Rewrite the response.** Unauthorized claims are redacted or replaced; authorized claims pass through.
4. **Log everything.** Every claim, every verdict, every citation appended to a regulator-readable audit log.

That's the whole product. No tool-call gating. No external-regulation grounding. No multi-modal. One job, done well.

---

## 4. Wedge ICP

US and EU banks deploying LLM-powered customer-facing or employee-facing systems where multi-user data is reachable. Concretely: private banking chatbots, retail support copilots, joint-account and family-banking flows, internal banker assistants with cross-customer access.

**Buyer:** CISO / Head of AI Risk / Chief Compliance Officer — operational-security budget, not Product or Legal.

**Activated regulatory frame:** NYDFS 23 NYCRR Part 500 (NPI exposure), GDPR Art. 5(1)(c) (data minimization), EU AI Act Annex III (high-risk financial systems), CFPB UDAAP coverage for any chatbot statement made to a consumer.

---

## 5. Open-source posture

Argo ships **Apache 2.0** from day one. The OSS core is the runnable product: gateway, claim extractor, entitlement-adapter interface, audit log, basic redaction policies. A bank's platform team can deploy it the same afternoon they discover it.

The commercial layer sits above the core: bank-grade entitlement-adapter packs (Okta, Entra, Auth0, Ping; core-banking integrations), the regulator-ready audit UI, multi-tenant SaaS control plane, SOC 2 / NYDFS / GDPR export templates, and managed operations.

The split is the dbt / HashiCorp / Snyk playbook: free runtime, paid governance and integrations.

---

## 6. Phase 0 — what we build first

A four-week, single-scenario demo:

- Private banking chatbot. Two users (A, B). Hard-coded entitlement bundles for each.
- User A asks a question whose naive answer would surface User B's account data.
- Argo intercepts, redacts, returns a clean response, logs the full decision trail.
- **Stack:** FastAPI gateway, LiteLLM proxy, Claude Haiku as claim-extraction judge, Postgres for audit log, minimal demo UI.
- **Deployment:** local dev machine, cloud LLM.
- **Output:** 60-second screen capture suitable for warm-intro CISO conversations.
- **Success metric:** 5 informal CISO conversations completed; ≥3 confirm output-side per-user filtering is a real, unmet need before any further build.

---

## 7. What Argo is explicitly *not*

- Not a content filter (Lakera, NeMo, LlamaGuard do this).
- Not a prompt-injection defense (handled by layers below us).
- Not a tool-call gate (different problem, different product).
- Not an external-regulation grounding engine (OFAC, FAA, state ABC — out of scope).
- Not a model-risk-management or AI-governance paperwork tool (Credo, Watsonx).
- Not an AI red-team or evaluation framework (Promptfoo, garak, Giskard).
- Not multi-modal in v1. Text in, text out.

The narrowness is the product.

---

## 8. Status & next steps

1. **Lock the wedge framing** — this doc.
2. **Confirm the project name** — Argo carries through; the acronym reworks cleanly as **A**uthorization, **R**edaction, **G**rounding, **O**versight. Open to change.
3. **Confirm OSS license** — lean Apache 2.0; revisit BSL only if a commercial-side risk surfaces later.
4. **Build Phase 0 demo** — four-week timebox; one scenario; one screen capture as the deliverable.
5. **Open five warm-intro CISO conversations** in parallel with the build; demo replaces slides.

---

One problem. One product. One ICP. One delivery model. Everything else cut.
