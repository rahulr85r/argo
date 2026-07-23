"""Pydantic models for claim extraction.

A Claim is one atomic fact the LLM response asserts about a subject. The
entitlement checker reads claims (it does not re-read the response prose) and
issues an ALLOW/REDACT/BLOCK verdict per claim. The rewriter splices verdicts
back into the response via source_span.

Design rules:
  - One claim = one fact about one subject. Multi-subject sentences are split.
  - `subject` is an identifier the EntitlementAdapter can resolve: a user_id
    (user_a/b/c), an account_id (acct_*), or a vendor display name string.
  - `role` is the disambiguator between "claim about subject's own data"
    (primary) and "claim about subject's appearance as counterparty on someone
    else's transaction" (counterparty). Counterparty role unlocks the
    counterparty_fields whitelist on the asking user's bundle.
  - `source_span` is an exact substring of the LLM response — used to locate
    and redact the text, and to verify the claim has any origin at all
    (hallucination check).
"""

from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal[
    "balance",
    "account_existence",
    "account_number",
    "account_ownership",
    "transaction",
    "display_name",
    "contact_email",
    "customer_status",
    "aggregate",
    "other",
]


ClaimRole = Literal["primary", "counterparty"]


class Claim(BaseModel):
    text: str = Field(..., description="Canonical one-line restatement of the claim.")
    subject: str = Field(
        ...,
        description=(
            "Identifier the entitlement layer resolves: user_id (user_a/b/c), "
            "account_id (acct_*), or vendor name. Use 'unknown' if the claim "
            "references a person/entity not in the dataset (hallucination)."
        ),
    )
    type: ClaimType
    role: ClaimRole = Field(
        ...,
        description=(
            "primary = claim about subject's own data. "
            "counterparty = subject appears as counterparty on the asking user's tx."
        ),
    )
    source_span: str = Field(
        ...,
        description="Exact substring of the response text this claim is derived from.",
    )


class ExtractedClaims(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
