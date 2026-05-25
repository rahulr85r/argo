# Policy reference

Argo's runtime behavior for any given user is shaped by exactly two
things: account ownership (data, in Postgres) and **the policy file**
(rules, in TOML). This document is the complete reference for that file.

If you only read one section, read [§1 — the two questions the policy
answers](#1-the-two-questions-the-policy-answers). The rest is detail.

The default policy lives at `argo/policy/banking.toml`. Production
deployments override the path via `POLICY_PATH=/etc/argo/policy.toml`
and keep their own file in their bank's policy repository.

---

## 1. The two questions the policy answers

Every gate decision boils down to two product questions. The policy
answers both:

**Q1 — Who counts as a "counterparty" of this user?**

A user can only be allowed to see *limited* information about another
person if that other person is one of their counterparties. The
`counterparty_rules` section names the relationships that confer this
status (recent payments, joint account ownership, etc.). Anyone not
matched by any rule is BLOCKED outright — the model cannot mention them
under counterparty role.

**Q2 — For someone who is a counterparty, which fields may surface?**

Even for valid counterparties, only certain claim types are disclosable.
The `counterparty_fields` section is the whitelist. Anything not on the
list is BLOCKED, regardless of how strong the counterparty relationship
is. This is the "whitelist not blacklist" design — explicit and
auditable.

A claim about *the asking user themselves* skips both Q1 and Q2 (it's
their own data — always ALLOWED). A claim about an entity that fails
either Q1 or Q2 is BLOCKED. A claim about a counterparty whose field
type IS on the whitelist passes the entitlement check, and if it's a
transaction claim, then heads to the verifier for source-span checking.

---

## 2. File format

TOML, parsed by Python's stdlib `tomllib`. The file has exactly two
top-level keys.

### 2.1 `counterparty_fields`

```toml
counterparty_fields = ["transaction", "account_ownership", "aggregate"]
```

A list of `ClaimType` strings. Every value must be one of:

| Value | What it means | Typical Phase-0 verdict |
|---|---|---|
| `transaction` | A tx the asking user is on, naming the counterparty (amount, date, direction, memo) | ALLOWED if on list (subject to verifier) |
| `account_ownership` | Naming a co-owner of an account the asking user also owns (e.g., joint) | ALLOWED if on list |
| `aggregate` | Totals / counts restricted to the asking-user ↔ counterparty interaction | ALLOWED if on list |
| `balance` | Account balance | BLOCKED — deliberately off list |
| `account_number` | Account number / last4 | BLOCKED — deliberately off list |
| `contact_email` | Email address | BLOCKED — deliberately off list |
| `customer_status` | Active/suspended/closed | BLOCKED — deliberately off list |
| `display_name` | Just the person's name | (always ALLOWED — handled separately as it's required for `transaction`) |
| `account_existence` | "Person X has an account" | BLOCKED for counterparties (off list) |
| `other` | Anything the extractor couldn't classify | BLOCKED |

**The full list of valid claim types is defined in `argo/claims.py` as
the `ClaimType` Literal.** Adding a new claim type requires a code change
there *and* a policy update here.

If you list a claim type the loader doesn't recognize, the gateway fails
at startup with:

```
policy banking.toml: unknown claim types in counterparty_fields: ['mortgage_balance']
```

That's intentional — it's better to fail loudly at startup than silently
ignore an unrecognized field.

### 2.2 `counterparty_rules`

```toml
[[counterparty_rules]]
type = "recent_payment"
lookback_days = 90

[[counterparty_rules]]
type = "joint_account_co_owner"
```

A TOML array-of-tables. Each table is one rule. Rules are **ORed** — a
user becomes a counterparty if they match at least one rule.

#### Available rule types

##### `recent_payment`

| Field | Required | Type | Example |
|---|---|---|---|
| `type` | yes | `"recent_payment"` | |
| `lookback_days` | yes | int | `90` |

Anyone who appeared as `counterparty_user_id` on any tx posted to one of
the asking user's accounts within the last `lookback_days` days becomes
a counterparty. The "now" reference is the database's `NOW()` (production)
or the seed's pinned reference date (tests).

Stale relationships drop off automatically. A user A who paid user J
$75 four years ago no longer has visibility into J today, even though
the tx is still in the history table.

##### `joint_account_co_owner`

| Field | Required | Type |
|---|---|---|
| `type` | yes | `"joint_account_co_owner"` |

Anyone who co-owns any account the asking user is on becomes a
counterparty. Independent of transactional activity — joint owners see
each other from the moment the joint is opened, before any tx posts.

If you remove this rule, joint account holders won't see each other as
counterparties until the joint posts a transaction that matches some
other rule. Probably not what you want.

#### Adding a new rule type

Two steps:

1. **Implement the SQL helper** in `argo/db/queries.py`:

   ```python
   def get_explicit_payee_counterparties(user_id: str) -> list[str]:
       """Rule `explicit_payee`: users on the asking user's saved-payees list."""
       with get_conn() as conn, conn.cursor() as cur:
           cur.execute(
               "SELECT payee_user_id FROM customer_payees WHERE customer_id = %s",
               (user_id,),
           )
           return [r["payee_user_id"] for r in cur.fetchall()]
   ```

2. **Wire it into the dispatch switch** in `argo/entitlements.py`
   (`DbDerivedAdapter._apply_rule`) and the policy loader's validator
   set in `argo/policy/__init__.py` (`_VALID_RULE_TYPES`).

After both changes are deployed, you can reference the new rule type in
`banking.toml`:

```toml
[[counterparty_rules]]
type = "explicit_payee"
```

The loader validates `type` against `_VALID_RULE_TYPES` at startup, so a
typo or a not-yet-deployed rule type fails fast with a clear error.

---

## 3. Worked examples

### 3.1 Minimal

The tightest plausible policy. Only joint co-owners are counterparties;
only transaction claims surface for them.

```toml
counterparty_fields = ["transaction"]

[[counterparty_rules]]
type = "joint_account_co_owner"
```

Effect: a user can ask about their joint accounts and the gate will
allow the co-owner's name on tx claims. Any other person is BLOCKED
because no rule matches. Account-ownership claims about the co-owner
(e.g., "Bob is on this joint") are BLOCKED because `account_ownership`
is not on the whitelist.

Useful as a sanity check or for a vertical that only wants joint-account
disclosure.

### 3.2 Phase-0 default

What ships in the repo:

```toml
counterparty_fields = ["transaction", "account_ownership", "aggregate"]

[[counterparty_rules]]
type = "recent_payment"
lookback_days = 90

[[counterparty_rules]]
type = "joint_account_co_owner"
```

Effect: someone counts as a counterparty if they're a joint co-owner OR
have transacted with the asking user in the last 90 days. For those
counterparties, transaction claims, account-ownership statements (e.g.,
"Bob is on this joint"), and aggregates (e.g., "you sent Charlie $X
total this month") are allowed. Balances, account numbers, emails,
customer status — all BLOCKED.

### 3.3 Plausible production banking

Wider relationship surface + more disclosable fields. (Note: `recent_*`
variants and `explicit_payee` are illustrative — you would need to add
the corresponding rule-type code per §2.2.)

```toml
counterparty_fields = [
    "transaction",
    "account_ownership",
    "aggregate",
    "display_name",   # explicitly allow naming counterparties even without an associated tx
]

[[counterparty_rules]]
# Recent P2P / Zelle / wire counterparties
type = "recent_payment"
lookback_days = 180

[[counterparty_rules]]
# Joint accounts always
type = "joint_account_co_owner"

[[counterparty_rules]]
# Customer's saved-payee list (rule type you implemented per §2.2)
type = "explicit_payee"
```

### 3.4 Strictest plausible

For very high-trust deployments (e.g., correspondent banking, where any
disclosure could be material). Only direct, current relationships count.

```toml
counterparty_fields = ["transaction"]

[[counterparty_rules]]
type = "recent_payment"
lookback_days = 30

[[counterparty_rules]]
type = "joint_account_co_owner"
```

---

## 4. Validation errors

The loader runs at startup and fails fast with descriptive errors. Each
maps to a clear cause.

| Error | Cause | Fix |
|---|---|---|
| `policy banking.toml: unknown claim types in counterparty_fields: [...]` | A value in `counterparty_fields` isn't in the `ClaimType` Literal. | Either fix the typo or add the type to `argo/claims.py`. |
| `policy banking.toml: unknown rule type 'foo' at index N; expected one of [...]` | A rule has a `type` value the loader doesn't recognize. | Either fix the typo or implement the rule type per §2.2. |
| `policy banking.toml: recent_payment rule at index N is missing required field 'lookback_days'` | The `recent_payment` rule was added but without its required field. | Add `lookback_days = N` to that rule. |
| `tomllib.TOMLDecodeError` | TOML syntax error. | Run your file through any TOML validator; the error includes the line number. |
| `FileNotFoundError: …banking.toml` | `POLICY_PATH` points at a file that doesn't exist (or isn't readable by the gateway process). | Fix the path or the file's permissions. |

---

## 5. Versioning your policy

The policy file is the GRC-reviewable artifact. Treat it like one.

**Do:**

- Keep it in version control (your bank's policy repo, not Argo's).
- Require PR review for every change. The PR should reference the
  control rationale.
- Tag releases of the policy. A policy version isn't the same as an Argo
  version — they evolve independently.
- Test policy changes against a staging Argo + a representative dataset
  before promoting to production.
- Record the active policy version in your audit pipeline (custom
  `AuditWriter` can hash the loaded file and include the digest in
  every audit event).

**Don't:**

- Edit the policy file on a running gateway. Policy is loaded at
  startup; the gateway will not pick up changes until restart. Make
  policy changes a deploy event.
- Use the same policy file across environments (dev/staging/prod). Use
  separate files; otherwise a typo blocks production.
- Hot-patch the policy in an incident. If you need to widen a rule to
  resolve a production issue, that's a policy change and goes through
  the same review. Argo's role is enforcing the policy, not bypassing
  it.

---

## 6. Things you cannot express today

Phase 0 deliberately keeps the policy minimal. If your requirements
include any of the below, you'll need either a code change (to add a new
rule type / claim type) or a custom `EntitlementAdapter` that ignores
some of the policy and applies its own logic:

- **Time-of-day restrictions.** No `business_hours_only` rule.
- **Tenant-specific overrides.** One policy file per gateway deployment;
  no per-tenant section.
- **Per-user overrides.** Policy applies uniformly to every user. Per-
  user nuance (VIP customer with broader counterparty visibility) lives
  in the adapter, not the policy.
- **Field-level masks** ("show Charlie's name but only the first
  initial"). Whitelist is yes/no per field; partial disclosure is not
  modeled.
- **Soft-blocks** ("warn but allow"). Verdicts are ALLOW / BLOCK / REDACT
  — there's no "warn" terminal.
- **Conditional rules** ("allow `aggregate` for counterparties only if
  the count > N"). Rule combinations are pure OR; richer logic needs an
  adapter.

If your bank needs these and you'd be interested in upstreaming, open an
issue with the use case before writing code — the abstraction may need to
shift to accommodate it.

---

## 7. Why TOML, not Rego or a custom DSL

A reasonable question: a real policy engine (OPA / Rego) is much more
expressive than TOML with a switch statement.

We chose TOML deliberately, for Phase 0:

- **Bank reviewers can read it without training.** TOML is closer to
  config than code. A GRC analyst signing off on a Rego policy needs to
  be a Rego programmer first; signing off on `lookback_days = 90` does
  not.
- **Validation at startup beats validation at runtime.** A typo in the
  TOML file is caught the moment the gateway boots, with a clear error.
  A bug in a Rego policy may not surface until the right combination of
  user + claim hits it.
- **The current rule set is small.** Two rule types and a few claim
  types do not need a Turing-complete policy language.
- **OPA can come later via an `EntitlementAdapter`.** A bank that
  already runs OPA can write `RegoEntitlementAdapter` that evaluates a
  Rego policy at request time and ignores `banking.toml` entirely.
  That's the same pattern as any other adapter swap.

When the rule set grows past ~10 types or banks want per-tenant logic,
we'll reconsider. Until then, TOML.
