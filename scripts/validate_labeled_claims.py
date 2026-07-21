"""Validate eval/labeled_claims.json against the Claim schema.

Checks:
  1. JSON parses.
  2. Each example has the expected top-level keys.
  3. Each claim parses against the Pydantic Claim model.
  4. Each source_span is an exact substring of its example's response.
  5. Prints summary: total examples, total claims, breakdown by category/type/role/subject.

Run: uv run python scripts/validate_labeled_claims.py
Exits non-zero on any failure.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from argo.claims import Claim

LABELED_PATH = Path(__file__).resolve().parent.parent / "eval" / "labeled_claims.json"


def main() -> int:
    raw = json.loads(LABELED_PATH.read_text())
    errors: list[str] = []

    cat_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    subject_counts: Counter[str] = Counter()
    claims_per_example: list[int] = []

    for ex in raw:
        ex_id = ex.get("id", "<missing id>")
        for required in ("id", "category", "user_id", "query", "response", "expected_claims"):
            if required not in ex:
                errors.append(f"[{ex_id}] missing top-level key '{required}'")

        cat_counts[ex.get("category", "?")] += 1
        claims_per_example.append(len(ex.get("expected_claims", [])))

        response = ex.get("response", "")
        for i, c in enumerate(ex.get("expected_claims", [])):
            try:
                parsed = Claim.model_validate(c)
            except Exception as e:
                errors.append(f"[{ex_id}#claim{i}] schema validation failed: {e}")
                continue

            type_counts[parsed.type] += 1
            role_counts[parsed.role] += 1
            subject_counts[parsed.subject] += 1

            if parsed.source_span not in response:
                errors.append(
                    f"[{ex_id}#claim{i}] source_span not found in response:\n"
                    f"    span: {parsed.source_span!r}"
                )

    print(f"Examples: {len(raw)}")
    print(f"  by category: {dict(cat_counts)}")
    total_claims = sum(claims_per_example)
    print(f"Claims total: {total_claims}")
    if claims_per_example:
        print(
            f"  per-example: min={min(claims_per_example)} "
            f"max={max(claims_per_example)} "
            f"avg={total_claims / len(claims_per_example):.1f}"
        )
    print(f"  by type: {dict(type_counts)}")
    print(f"  by role: {dict(role_counts)}")
    print(f"  by subject: {dict(subject_counts)}")

    if errors:
        print("\nERRORS:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nOK — all examples parsed and all source_spans found in their responses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
