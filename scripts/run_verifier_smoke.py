"""Smoke-test the source-span verifier against labeled transaction claims.

This is the demo headliner test: Q3 must yield exactly one ALLOW (the real
May-12 A→C $45 groceries share) and three REDACTs (the three hallucinations
/ misattributions / direction-reversals).

Usage:
    uv run python scripts/run_verifier_smoke.py
    uv run python scripts/run_verifier_smoke.py --only b3_q3_money_sent_lastweek
    uv run python scripts/run_verifier_smoke.py --verbose

Exits non-zero if any expected verdict mismatches.
"""

import argparse
import json
import sys
from pathlib import Path

from argo.claims import Claim
from argo.db.queries import PostgresTransactionSource
from argo.entitlements import ClaimVerdict
from argo.verifier import verify_transaction_claims


TX_SOURCE = PostgresTransactionSource()


REPO = Path(__file__).resolve().parent.parent
LABELED = json.loads((REPO / "eval" / "labeled_claims.json").read_text())
LABELED_BY_ID = {ex["id"]: ex for ex in LABELED}


# Per-claim expected verifier verdict. ONLY transaction claims that the
# entitlement layer routes to NEEDS_SOURCE_CHECK are listed here. Other
# claims never reach the verifier.
#
# Keys are (example_id, claim_index); values are ALLOW or REDACT.
EXPECTED: dict[tuple[str, int], ClaimVerdict] = {
    # Q3 — demo headliner
    ("b3_q3_money_sent_lastweek", 0): ClaimVerdict.REDACT,   # May 17 A→B $250 — hallucinated
    ("b3_q3_money_sent_lastweek", 1): ClaimVerdict.REDACT,   # May 16 A→C $189 REI — hallucinated
    ("b3_q3_money_sent_lastweek", 2): ClaimVerdict.REDACT,   # May 15 A→C $300 Tahoe — direction reversed (real is C→A)
    ("b3_q3_money_sent_lastweek", 3): ClaimVerdict.ALLOW,    # May 12 A→C $45 groceries — real

    # Counterparty txs that DO exist
    ("e09_counterparty_allowed_minimal", 0): ClaimVerdict.ALLOW,   # May 12 A→C $45
    ("e11_inbound_from_counterparty", 0): ClaimVerdict.ALLOW,      # May 15 C→A $300 inbound
    ("e16_external_vendor_only_own_data", 0): ClaimVerdict.ALLOW,  # May 1 WF $2,800 from joint
    ("e19_aliased_name_inbound", 0): ClaimVerdict.ALLOW,           # May 4 Rivera Design $2,400 → Charlie's account
    ("e04_confusion_vendor_rivera_studios", 0): ClaimVerdict.ALLOW, # May 17 Bob→Rivera Studios $250
    ("e05_confusion_vendor_chens_tea", 0): ClaimVerdict.ALLOW,      # Feb 18 Alice→Chen's Tea House $42.50
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="restrict to one example id")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Group expected entries by example id so we can batch one verifier call per response.
    by_ex: dict[str, list[tuple[int, ClaimVerdict]]] = {}
    for (ex_id, idx), expected in EXPECTED.items():
        by_ex.setdefault(ex_id, []).append((idx, expected))
    if args.only:
        by_ex = {k: v for k, v in by_ex.items() if k == args.only}
        if not by_ex:
            print(f"no expected verdicts for id={args.only!r}")
            return 1

    failures: list[str] = []
    total = 0

    for ex_id, items in by_ex.items():
        ex = LABELED_BY_ID[ex_id]
        items.sort(key=lambda x: x[0])
        claims = [Claim.model_validate(ex["expected_claims"][i]) for i, _ in items]
        expecteds = [v for _, v in items]

        results = verify_transaction_claims(claims, ex["user_id"], TX_SOURCE)

        for (i, expected), claim, result in zip(items, claims, results, strict=True):
            total += 1
            ok = result.verdict == expected
            marker = "✓" if ok else "✗"
            cid = f"{ex_id}#{i}"
            print(f"  {marker} {cid:42s} expected={expected.value:7s} got={result.verdict.value:7s}")
            if args.verbose or not ok:
                print(f"      claim: {claim.text[:100]}")
                print(f"      reason: {result.reason}")
                if result.matched_tx_id:
                    print(f"      matched_tx_id: {result.matched_tx_id}")
            if not ok:
                failures.append(cid)

    print()
    print("=" * 60)
    if failures:
        print(f"FAIL — {len(failures)}/{total} verdicts wrong: {failures}")
        return 1
    print(f"PASS — {total}/{total} verdicts match expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
