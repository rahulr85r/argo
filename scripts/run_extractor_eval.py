"""Run the Haiku claim extractor against eval/labeled_claims.json and score it.

Usage:
    uv run python scripts/run_extractor_eval.py                    # all 26 examples
    uv run python scripts/run_extractor_eval.py --only b3_q3       # one example
    uv run python scripts/run_extractor_eval.py --limit 5          # first 5
    uv run python scripts/run_extractor_eval.py --save runs/v1.json

Scoring rubric:
    A predicted claim matches a labeled claim iff:
        - subject equal (exact)
        - type equal (exact)
        - role equal (exact)
    Each labeled / predicted claim is matched to at most one counterpart
    (greedy by first match). source_span is logged but not required for the
    match — overlap with the labeled span is reported separately.

    TP = labeled claims matched
    FN = labeled claims unmatched (missed)
    FP = predicted claims unmatched (hallucinated by extractor)
    Recall = TP / (TP + FN)
    Precision = TP / (TP + FP)

    Confusion-vendor accuracy is reported separately: for examples e04, e05,
    e19 the subject of every predicted claim must NOT be a user_id (must be a
    vendor string or unknown).
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from argo.claims import Claim
from argo.judge import extract_claims_raw

REPO = Path(__file__).resolve().parent.parent
LABELED_PATH = REPO / "eval" / "labeled_claims.json"
CONFUSION_VENDOR_IDS = {"e04_confusion_vendor_rivera_studios",
                        "e05_confusion_vendor_chens_tea",
                        "e19_aliased_name_inbound"}


def _key(c: Claim) -> tuple[str, str, str]:
    return (c.subject, c.type, c.role)


def _span_overlap(a: str, b: str) -> bool:
    """True if either span is a substring of the other, or they share >50% chars."""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # crude overlap heuristic for fuzzy span matches
    short, long_ = sorted([a, b], key=len)
    return any(short[i : i + max(20, len(short) // 2)] in long_ for i in range(0, len(short), 10))


def score_example(labeled: list[Claim], predicted: list[Claim]) -> dict:
    """Greedy match labeled→predicted by (subject, type, role)."""
    used_pred: set[int] = set()
    matches: list[tuple[Claim, Claim]] = []
    misses: list[Claim] = []

    for lab in labeled:
        for j, pred in enumerate(predicted):
            if j in used_pred:
                continue
            if _key(lab) == _key(pred):
                matches.append((lab, pred))
                used_pred.add(j)
                break
        else:
            misses.append(lab)

    extras = [p for j, p in enumerate(predicted) if j not in used_pred]

    span_ok = sum(1 for lab, pred in matches if _span_overlap(lab.source_span, pred.source_span))

    return {
        "tp": len(matches),
        "fn": len(misses),
        "fp": len(extras),
        "span_overlaps": span_ok,
        "matches": matches,
        "misses": misses,
        "extras": extras,
    }


def fmt_claim(c: Claim) -> str:
    return f"({c.subject}, {c.type}, {c.role}) :: {c.text[:70]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run a single example by id")
    parser.add_argument("--limit", type=int, help="run only the first N examples")
    parser.add_argument("--save", help="write per-example predictions to this JSON file")
    parser.add_argument("--verbose", action="store_true", help="show every match too, not just misses")
    args = parser.parse_args()

    examples = json.loads(LABELED_PATH.read_text())
    if args.only:
        examples = [e for e in examples if e["id"] == args.only]
        if not examples:
            print(f"no example with id={args.only!r}")
            return 1
    if args.limit:
        examples = examples[: args.limit]

    overall_tp = overall_fn = overall_fp = overall_span = 0
    by_type_tp: Counter[str] = Counter()
    by_type_fn: Counter[str] = Counter()
    by_type_fp: Counter[str] = Counter()
    cv_pass = cv_fail = 0
    cv_failures: list[tuple[str, str]] = []
    save_payload: list[dict] = []
    parse_errors: list[str] = []
    total_latency = 0
    t_start = time.perf_counter()

    for ex in examples:
        ex_id = ex["id"]
        labeled = [Claim.model_validate(c) for c in ex["expected_claims"]]
        result_predicted, raw, latency = extract_claims_raw(ex["response"], ex["user_id"])
        total_latency += latency

        if result_predicted is None:
            parse_errors.append(f"{ex_id}: parse failure / raw output:\n{raw[:400]}\n")
            predicted: list[Claim] = []
        else:
            predicted = result_predicted.claims

        s = score_example(labeled, predicted)
        overall_tp += s["tp"]
        overall_fn += s["fn"]
        overall_fp += s["fp"]
        overall_span += s["span_overlaps"]

        for lab, _ in s["matches"]:
            by_type_tp[lab.type] += 1
        for lab in s["misses"]:
            by_type_fn[lab.type] += 1
        for pred in s["extras"]:
            by_type_fp[pred.type] += 1

        if ex_id in CONFUSION_VENDOR_IDS:
            user_subjects = [p for p in predicted if p.subject in {"user_a", "user_b", "user_c"}]
            if user_subjects:
                cv_fail += 1
                for p in user_subjects:
                    cv_failures.append((ex_id, fmt_claim(p)))
            else:
                cv_pass += 1

        save_payload.append({
            "id": ex_id,
            "user_id": ex["user_id"],
            "latency_ms": latency,
            "predicted": [p.model_dump() for p in predicted],
            "raw": raw if result_predicted is None else None,
            "tp": s["tp"], "fn": s["fn"], "fp": s["fp"],
        })

        verdict = f"tp={s['tp']} fn={s['fn']} fp={s['fp']}"
        print(f"  {ex_id:42s} {verdict}  ({latency} ms)")
        if args.verbose:
            for lab, pred in s["matches"]:
                print(f"      ✓ {fmt_claim(lab)}")
                if not _span_overlap(lab.source_span, pred.source_span):
                    print("        span mismatch:")
                    print(f"          labeled: {lab.source_span!r}")
                    print(f"          pred:    {pred.source_span!r}")
        for m in s["misses"]:
            print(f"      MISS: {fmt_claim(m)}")
        for e in s["extras"]:
            print(f"      EXTRA: {fmt_claim(e)}")

    wall = time.perf_counter() - t_start
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Examples: {len(examples)}   total wall: {wall:.1f}s   total LLM latency: {total_latency/1000:.1f}s")
    print(f"Claims labeled total: {overall_tp + overall_fn}")
    print(f"Claims predicted total: {overall_tp + overall_fp}")
    print(f"TP={overall_tp}  FN={overall_fn}  FP={overall_fp}  span-overlaps={overall_span}/{overall_tp}")
    if overall_tp + overall_fn > 0:
        print(f"Recall:    {overall_tp / (overall_tp + overall_fn):.2%}")
    if overall_tp + overall_fp > 0:
        print(f"Precision: {overall_tp / (overall_tp + overall_fp):.2%}")

    print("\nPer-type breakdown:")
    all_types = set(by_type_tp) | set(by_type_fn) | set(by_type_fp)
    print(f"  {'type':<22}{'TP':>5}{'FN':>5}{'FP':>5}{'recall':>10}")
    for t in sorted(all_types):
        tp, fn, fp = by_type_tp[t], by_type_fn[t], by_type_fp[t]
        recall = f"{tp / (tp + fn):.0%}" if (tp + fn) else "n/a"
        print(f"  {t:<22}{tp:>5}{fn:>5}{fp:>5}{recall:>10}")

    print(f"\nConfusion-vendor accuracy: {cv_pass}/{cv_pass + cv_fail}")
    for ex_id, claim_str in cv_failures:
        print(f"  FAIL {ex_id}: {claim_str}")

    if parse_errors:
        print(f"\nParse errors ({len(parse_errors)}):")
        for pe in parse_errors:
            print(f"  {pe}")

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(save_payload, indent=2))
        print(f"\nSaved per-example predictions to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
