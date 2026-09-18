#!/usr/bin/env python3
"""CI smoke regression gate — run a fixed 5-question subset and compare to baseline.

Usage (repo root, DB up, provider key configured):
  .venv/bin/python eval/run_smoke.py
  .venv/bin/python eval/run_smoke.py --update-baseline   # manual only; never in CI
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from eval_harness import (  # noqa: E402
    RESULTS_DIR,
    load_benchmark,
    run_benchmark,
)

SMOKE_SUBSET_PATH = ROOT / "eval" / "smoke_subset.json"
BASELINE_PATH = RESULTS_DIR / "smoke_baseline.json"
SMOKE_REPORT_PATH = RESULTS_DIR / "smoke_report.md"
SMOKE_RAW_PATH = RESULTS_DIR / "smoke_latest_run.json"


def load_smoke_subset(path: Path = SMOKE_SUBSET_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data.get("question_ids"):
        raise TypeError(f"{path} must be an object with a non-empty question_ids list")
    return data


def resolve_questions(subset: dict[str, Any]) -> list[dict[str, Any]]:
    bench_rel = subset.get("benchmark") or "eval/benchmark_v2.json"
    bench_path = (
        ROOT / bench_rel if not Path(bench_rel).is_absolute() else Path(bench_rel)
    )
    questions = load_benchmark(bench_path)
    by_id = {q["id"]: q for q in questions}
    wanted = [str(qid) for qid in subset["question_ids"]]
    missing = [qid for qid in wanted if qid not in by_id]
    if missing:
        raise SystemExit(f"Smoke subset ids not in {bench_path}: {', '.join(missing)}")
    return [by_id[qid] for qid in wanted]


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    baseline_path = path or BASELINE_PATH
    if not baseline_path.exists():
        raise SystemExit(
            f"Missing baseline at {baseline_path}. "
            "Run with --update-baseline after a green smoke run."
        )
    data = json.loads(baseline_path.read_text())
    if "passed" not in data or "total" not in data:
        raise SystemExit(
            f"Baseline {baseline_path} must include integer 'passed' and 'total'"
        )
    return data


def write_baseline(
    *,
    passed: int,
    total: int,
    question_ids: list[str],
    rows: list[dict[str, Any]],
    path: Path | None = None,
) -> None:
    baseline_path = path or BASELINE_PATH
    payload = {
        "passed": passed,
        "total": total,
        "score": f"{passed}/{total}",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "subset": "eval/smoke_subset.json",
        "benchmark": "eval/benchmark_v2.json",
        "question_ids": question_ids,
        "per_question": [
            {"id": r["id"], "passed": bool(r.get("passed"))} for r in rows
        ],
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Updated baseline: {baseline_path} ({payload['score']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run CI smoke subset and gate on stored baseline score"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite eval/results/smoke_baseline.json from this run (manual only)",
    )
    parser.add_argument(
        "--subset",
        type=Path,
        default=SMOKE_SUBSET_PATH,
        help="Path to smoke_subset.json",
    )
    args = parser.parse_args(argv)

    subset = load_smoke_subset(args.subset)
    questions = resolve_questions(subset)
    question_ids = [q["id"] for q in questions]

    print(f"Smoke subset: {', '.join(question_ids)}")
    rows, passed, total = run_benchmark(
        questions,
        report_path=SMOKE_REPORT_PATH,
        raw_path=SMOKE_RAW_PATH,
    )
    score = f"{passed}/{total}"
    print(f"Smoke score: {score}")

    if args.update_baseline:
        if passed < total:
            print(
                f"Refusing to write baseline from a partial smoke run ({score}). "
                "Fix failures, then re-run --update-baseline.",
                file=sys.stderr,
            )
            return 1
        write_baseline(
            passed=passed,
            total=total,
            question_ids=question_ids,
            rows=rows,
        )
        return 0

    baseline = load_baseline()
    base_passed = int(baseline["passed"])
    base_total = int(baseline["total"])
    print(f"Baseline: {base_passed}/{base_total} (updated {baseline.get('updated')})")

    if total != base_total:
        print(
            f"FAIL: smoke ran {total} questions but baseline expects {base_total}. "
            "Re-run with --update-baseline after intentional subset changes.",
            file=sys.stderr,
        )
        return 1

    if passed < base_passed:
        print(
            f"FAIL: smoke score {score} dropped below baseline "
            f"{base_passed}/{base_total}.",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: smoke score {score} meets baseline {base_passed}/{base_total}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
