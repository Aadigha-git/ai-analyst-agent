"""Evaluation harness — run benchmark questions through the agent and grade rubrics.

Usage (repo root, DB up, .env configured):
  NEBIUS_TIMEOUT_SECONDS=180 .venv/bin/python eval/eval_harness.py
  .venv/bin/python eval/eval_harness.py --benchmark eval/benchmark_v2.json

Cross-provider comparison (manual / offline only — NEVER invoke from CI):
  .venv/bin/python eval/eval_harness.py --compare-providers

``--compare-providers`` makes real API calls to every configured provider against the
full v2 suite (~30 questions each) and incurs real (small) LLM cost. CI must only
run the 5-question smoke subset via ``eval/run_smoke.py`` (v2-4).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from llm_client import (  # noqa: E402
    DEFAULT_MODELS,
    PROVIDER_API_KEY_ENV,
    LLMProvider,
    LLMResponse,
    get_llm_provider,
)
from orchestrator.loop import investigate  # noqa: E402
from tools.schema_introspector import clear_schema_cache  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eval_harness")

BENCHMARK_PATH = ROOT / "eval" / "benchmark_questions.json"
BENCHMARK_V2_PATH = ROOT / "eval" / "benchmark_v2.json"
RESULTS_DIR = ROOT / "eval" / "results"
REPORT_PATH = RESULTS_DIR / "report.md"
RAW_PATH = RESULTS_DIR / "latest_run.json"
COMPARISON_PATH = RESULTS_DIR / "comparison_v2.md"

# Providers compared by --compare-providers (order preserved in the report).
COMPARE_PROVIDERS = ("nebius", "openai", "anthropic", "google")

_GRADER_SYSTEM = (
    "You are a strict evaluation grader for a data-analyst agent. "
    "Score ONLY against the provided expected_answer and rubric criteria. "
    "Do not reward confident-sounding prose that fails the criteria. "
    "Reply with a single JSON object (no markdown) of the form:\n"
    "{"
    '"passed": true|false, '
    '"criteria": [{"criterion": str, "met": true|false, "note": str}], '
    '"failure_category": null|"multi_step"|"verification"|"trap_handling"|"factual"|"other", '
    '"summary": str'
    "}. "
    "Set passed=true only if every rubric criterion is met. "
    "IMPORTANT grading rules: "
    "(1) If agent_status is ok/needs_clarification and the agent_answer matches the "
    "expected_answer on the key numeric/entity facts, mark criteria about which SQL "
    "table was used as met — do NOT fail solely because the narrative omits table names. "
    "(2) For trap questions, prefer clarification or an explicit stated assumption. "
    "(3) For trap_handling when the agent should clarify but guesses instead. "
    "Use multi_step when joins/second queries are wrong or missing. "
    "Use verification when a draft answer conflicts with evidence/verification. "
    "Use factual for wrong numeric/entity answers on otherwise well-formed single-step work."
)


def load_benchmark(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    """Load a benchmark file (v1 JSON array or v2 `{questions: [...]}` object)."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("questions"), list):
        return data["questions"]
    raise TypeError(
        f"{path} must be a JSON array or an object with a 'questions' array"
    )


def extract_final_answer(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "ok":
        answer = str(result.get("answer") or "").strip()
        defaults = [
            str(d).strip()
            for d in (result.get("defaults_used") or [])
            if str(d).strip()
        ]
        if not defaults:
            return answer
        lines = [answer] if answer else []
        for item in defaults:
            lines.append(
                item
                if item.lower().startswith("assumption:")
                else f"Assumption: {item}"
            )
        return "\n".join(lines).strip()
    if status == "needs_clarification":
        parts = [
            f"[needs_clarification] {result.get('clarifying_question') or ''}".strip()
        ]
        if result.get("reason"):
            parts.append(f"Reason: {result['reason']}")
        return "\n".join(parts).strip()
    if status == "uncertain":
        return f"[uncertain] {result.get('message') or 'No confident answer.'}"
    if status == "verification_failed":
        detail = (result.get("verification") or {}).get("detail")
        return f"[verification_failed] draft={result.get('answer') or ''}" + (
            f"; detail={detail}" if detail else ""
        )
    if status == "error":
        return f"[error] {result.get('message') or 'unknown error'}"
    return json.dumps(result, default=str)[:2000]


def _parse_grader_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        import re

        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def grade_with_llm(
    question: dict[str, Any],
    agent_answer: str,
    agent_status: str,
    *,
    client: LLMProvider,
) -> dict[str, Any]:
    payload = {
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "rubric": question["rubric"],
        "requires_multi_step": question.get("requires_multi_step"),
        "is_trap": question.get("is_trap"),
        "agent_status": agent_status,
        "agent_answer": agent_answer,
    }
    result: LLMResponse = client.chat(
        system_prompt=_GRADER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Grade this agent response against the rubric.\n"
                    f"{json.dumps(payload, indent=2)}\n"
                ),
            }
        ],
        tools=None,
    )
    try:
        graded = _parse_grader_json(result.text or "")
    except json.JSONDecodeError:
        graded = {
            "passed": False,
            "criteria": [
                {
                    "criterion": c,
                    "met": False,
                    "note": "Grader returned non-JSON; marked unmet.",
                }
                for c in question["rubric"]
            ],
            "failure_category": "other",
            "summary": f"Unparseable grader output: {(result.text or '')[:300]}",
        }

    # Enforce structured pass: all criteria met if criteria list present.
    criteria = graded.get("criteria") or []
    if criteria and isinstance(criteria, list):
        graded["passed"] = all(
            bool(c.get("met")) for c in criteria if isinstance(c, dict)
        )
    else:
        graded["passed"] = bool(graded.get("passed"))
        graded["criteria"] = [
            {"criterion": c, "met": graded["passed"], "note": ""}
            for c in question["rubric"]
        ]
    if graded["passed"]:
        graded["failure_category"] = None
    elif not graded.get("failure_category"):
        if question.get("is_trap"):
            graded["failure_category"] = "trap_handling"
        elif question.get("requires_multi_step"):
            graded["failure_category"] = "multi_step"
        else:
            graded["failure_category"] = "factual"
    return graded


def run_one(
    question: dict[str, Any],
    *,
    client: LLMProvider,
) -> dict[str, Any]:
    clear_schema_cache()
    qid = question["id"]
    logger.info("Running %s: %s", qid, question["question"])
    try:
        agent_result = investigate(question["question"], client=client)
    except Exception as exc:  # noqa: BLE001
        agent_result = {
            "status": "error",
            "message": str(exc),
            "answer": None,
            "trace": [],
        }

    final_answer = extract_final_answer(agent_result)
    status = str(agent_result.get("status") or "error")
    try:
        grade = grade_with_llm(question, final_answer, status, client=client)
    except Exception as exc:  # noqa: BLE001
        grade = {
            "passed": False,
            "criteria": [
                {"criterion": c, "met": False, "note": "grader error"}
                for c in question["rubric"]
            ],
            "failure_category": "other",
            "summary": f"Grader failed: {exc}",
        }

    return {
        "id": qid,
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "requires_multi_step": bool(question.get("requires_multi_step")),
        "is_trap": bool(question.get("is_trap")),
        "agent_status": status,
        "agent_answer": final_answer,
        "iterations": agent_result.get("iterations"),
        "sql_steps": agent_result.get("sql_steps"),
        "verification": agent_result.get("verification"),
        "grade": grade,
        "passed": bool(grade.get("passed")),
        "failure_category": grade.get("failure_category"),
    }


def write_report(rows: list[dict[str, Any]], path: Path = REPORT_PATH) -> None:
    total = len(rows)
    passed = sum(1 for r in rows if r.get("passed"))
    lines: list[str] = []
    lines.append("# Evaluation Report")
    lines.append("")
    lines.append(
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    lines.append("")
    lines.append(f"## Overall score: **{passed}/{total}**")
    lines.append("")
    lines.append("| ID | Pass | Multi-step | Trap | Agent status | Failure category |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        lines.append(
            "| {id} | {pass_} | {multi} | {trap} | {status} | {cat} |".format(
                id=r["id"],
                pass_="PASS" if r.get("passed") else "FAIL",
                multi="yes" if r.get("requires_multi_step") else "no",
                trap="yes" if r.get("is_trap") else "no",
                status=r.get("agent_status") or "",
                cat=r.get("failure_category") or "—",
            )
        )
    lines.append("")
    lines.append("## Failure notes")
    lines.append("")
    failures = [r for r in rows if not r.get("passed")]
    if not failures:
        lines.append("No failures.")
    else:
        for r in failures:
            cat = r.get("failure_category") or "other"
            lines.append(f"### {r['id']} — `{cat}`")
            lines.append("")
            lines.append(f"- **Question:** {r['question']}")
            lines.append(f"- **Expected:** {r['expected_answer']}")
            lines.append(
                f"- **Agent ({r.get('agent_status')}):** {r.get('agent_answer')}"
            )
            summary = (r.get("grade") or {}).get("summary") or ""
            if summary:
                lines.append(f"- **Grader:** {summary}")
            # Classify note for the report requirement
            if cat == "multi_step":
                lines.append(
                    "- **Note:** Failure attributed to multi-step reasoning "
                    "(join / second-query path)."
                )
            elif cat == "verification":
                lines.append(
                    "- **Note:** Failure attributed to verification "
                    "(draft inconsistent with independent check)."
                )
            elif cat == "trap_handling":
                lines.append(
                    "- **Note:** Failure attributed to trap-question handling "
                    "(should clarify or state assumptions; guessed instead)."
                )
            else:
                lines.append(f"- **Note:** Failure category `{cat}`.")
            lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    logger.info("Wrote %s", path)


def run_benchmark(
    questions: list[dict[str, Any]],
    *,
    client: LLMProvider | None = None,
    report_path: Path = REPORT_PATH,
    raw_path: Path = RAW_PATH,
) -> tuple[list[dict[str, Any]], int, int]:
    """Run ``questions`` through the agent + grader; return (rows, passed, total)."""
    llm = client or get_llm_provider()
    rows: list[dict[str, Any]] = []
    for q in questions:
        row = run_one(q, client=llm)
        rows.append(row)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(rows, indent=2, default=str))
        logger.info(
            "%s => %s (%s)",
            row["id"],
            "PASS" if row["passed"] else "FAIL",
            row.get("failure_category"),
        )

    write_report(rows, path=report_path)
    passed = sum(1 for r in rows if r["passed"])
    return rows, passed, len(rows)


def provider_api_key_configured(provider: str) -> bool:
    """Return True when the provider's env API key is non-empty."""
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    if not key_env:
        return False
    return bool(os.getenv(key_env, "").strip())


def _pass_map(rows: list[dict[str, Any]]) -> dict[str, bool]:
    return {str(r["id"]): bool(r.get("passed")) for r in rows}


def write_comparison_report(
    results_by_provider: dict[str, list[dict[str, Any]]],
    *,
    questions: list[dict[str, Any]],
    skipped: list[tuple[str, str]],
    path: Path = COMPARISON_PATH,
) -> None:
    """Write provider×score table plus notable pass/fail disagreements."""
    question_meta = {str(q["id"]): q for q in questions}
    lines: list[str] = []
    lines.append("# Cross-model evaluation comparison (v2)")
    lines.append("")
    lines.append(
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    lines.append("")
    lines.append(
        "Manual / offline only — incurs real LLM API cost. "
        "Not run in CI (smoke subset only)."
    )
    lines.append("")
    lines.append("## Provider scores")
    lines.append("")
    lines.append("| Provider | Score | Model |")
    lines.append("| --- | --- | --- |")

    for provider, rows in results_by_provider.items():
        total = len(rows)
        passed = sum(1 for r in rows if r.get("passed"))
        model = DEFAULT_MODELS.get(provider, "(default)")
        lines.append(f"| {provider} | **{passed}/{total}** | `{model}` |")

    for provider, reason in skipped:
        lines.append(f"| {provider} | _skipped_ | {reason} |")

    lines.append("")
    lines.append("## Notable differences")
    lines.append("")

    providers = list(results_by_provider.keys())
    if len(providers) < 2:
        lines.append(
            "Fewer than two providers produced scores, so pass/fail disagreements "
            "cannot be compared."
        )
    else:
        pass_maps = {p: _pass_map(rows) for p, rows in results_by_provider.items()}
        all_ids = sorted(
            {qid for pm in pass_maps.values() for qid in pm},
            key=lambda qid: (
                int(qid.split("-")[-1]) if qid.split("-")[-1].isdigit() else 999,
                qid,
            ),
        )
        disagreements: list[str] = []
        glossary_trap_notes: list[str] = []
        for qid in all_ids:
            outcomes = {
                p: pass_maps[p].get(qid) for p in providers if qid in pass_maps[p]
            }
            if len(set(outcomes.values())) <= 1:
                continue
            meta = question_meta.get(qid) or {}
            verdict = ", ".join(
                f"{p}={'PASS' if ok else 'FAIL'}" for p, ok in outcomes.items()
            )
            flags: list[str] = []
            if meta.get("tests_glossary"):
                flags.append("glossary-default / disclosure")
            if meta.get("is_trap"):
                flags.append("trap")
            flag_note = f" ({'; '.join(flags)})" if flags else ""
            bullet = f"- **{qid}**{flag_note}: {meta.get('question', '')} — {verdict}"
            disagreements.append(bullet)
            if flags:
                glossary_trap_notes.append(bullet)

        if not disagreements:
            lines.append(
                "No pass/fail disagreements across providers that completed the suite."
            )
        else:
            lines.append(
                "Questions where providers disagreed on pass/fail "
                "(glossary-default disclosure and trap items called out first when present):"
            )
            lines.append("")
            # Prefer glossary/trap disagreements at the top of the section.
            for bullet in glossary_trap_notes:
                lines.append(bullet)
            for bullet in disagreements:
                if bullet not in glossary_trap_notes:
                    lines.append(bullet)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    logger.info("Wrote %s", path)


def run_compare_providers(
    questions: list[dict[str, Any]],
    *,
    providers: tuple[str, ...] = COMPARE_PROVIDERS,
    comparison_path: Path = COMPARISON_PATH,
) -> int:
    """Run the full suite once per provider and write ``comparison_v2.md``.

    WARNING: Makes real multi-provider API calls and costs real (small) money.
    Intended for manual / nightly use only — never invoke from CI. The PR smoke
    gate (v2-4) runs ``eval/run_smoke.py`` on a 5-question subset instead.
    """
    results_by_provider: dict[str, list[dict[str, Any]]] = {}
    skipped: list[tuple[str, str]] = []

    for provider in providers:
        key_env = PROVIDER_API_KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
        if not provider_api_key_configured(provider):
            msg = f"`{key_env}` not set"
            logger.warning("Skipping provider %s — %s", provider, msg)
            print(f"WARN: skipping {provider} ({msg})", file=sys.stderr)
            skipped.append((provider, msg))
            continue

        # Use each provider's default model so a global LLM_MODEL (e.g. Nebius id)
        # does not get applied to OpenAI/Anthropic/Google.
        model = DEFAULT_MODELS.get(provider)
        logger.info(
            "=== compare-providers: starting %s (model=%s) ===", provider, model
        )
        try:
            client = get_llm_provider(provider, model=model)
        except Exception as exc:  # noqa: BLE001
            msg = f"failed to init: {exc}"
            logger.warning("Skipping provider %s — %s", provider, msg)
            print(f"WARN: skipping {provider} ({msg})", file=sys.stderr)
            skipped.append((provider, msg))
            continue

        report_path = RESULTS_DIR / f"report_{provider}_v2.md"
        raw_path = RESULTS_DIR / f"latest_run_{provider}_v2.json"
        try:
            rows, passed, total = run_benchmark(
                questions,
                client=client,
                report_path=report_path,
                raw_path=raw_path,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"run failed: {exc}"
            logger.exception("Provider %s aborted", provider)
            print(f"WARN: skipping remainder for {provider} ({msg})", file=sys.stderr)
            skipped.append((provider, msg))
            continue

        results_by_provider[provider] = rows
        print(f"{provider}: {passed}/{total}")

    write_comparison_report(
        results_by_provider,
        questions=questions,
        skipped=skipped,
        path=comparison_path,
    )
    print(f"Comparison report: {comparison_path}")
    # Partial provider coverage is OK — do not fail the whole run for missing keys.
    return 0 if results_by_provider else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run benchmark eval harness")
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=BENCHMARK_PATH,
        help="Path to benchmark JSON (v1 array or v2 object with questions)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on number of questions (0 = all)",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated question ids to run (optional)",
    )
    parser.add_argument(
        "--compare-providers",
        action="store_true",
        help=(
            "Run benchmark_v2 once per configured provider and write "
            "eval/results/comparison_v2.md. Real multi-provider API cost — "
            "manual/nightly only; never used by CI (see eval/run_smoke.py)."
        ),
    )
    args = parser.parse_args(argv)

    if args.compare_providers:
        # Always use the v2 suite for cross-model comparison unless overridden.
        bench_path = (
            args.benchmark if args.benchmark != BENCHMARK_PATH else BENCHMARK_V2_PATH
        )
        questions = load_benchmark(bench_path)
        if args.ids.strip():
            wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
            questions = [q for q in questions if q["id"] in wanted]
        if args.limit and args.limit > 0:
            questions = questions[: args.limit]
        return run_compare_providers(questions)

    questions = load_benchmark(args.benchmark)
    if args.ids.strip():
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            raise SystemExit(f"Unknown question id(s): {', '.join(sorted(missing))}")
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    # Prefer report_v2.md when scoring the versioned v2 suite.
    report_path = REPORT_PATH
    raw_path = RAW_PATH
    if Path(args.benchmark).resolve() == BENCHMARK_V2_PATH.resolve() or (
        Path(args.benchmark).name == "benchmark_v2.json"
    ):
        report_path = RESULTS_DIR / "report_v2.md"
        raw_path = RESULTS_DIR / "latest_run_v2.json"

    rows, passed, total = run_benchmark(
        questions, report_path=report_path, raw_path=raw_path
    )
    print(f"Score: {passed}/{total}")
    print(f"Report: {report_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
