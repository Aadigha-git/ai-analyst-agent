"""Evaluation harness — run benchmark questions through the agent and grade rubrics.

Usage (repo root, DB up, .env configured):
  NEBIUS_TIMEOUT_SECONDS=180 .venv/bin/python eval/eval_harness.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from llm_client import LLMProvider, LLMResponse, get_llm_provider  # noqa: E402
from orchestrator.loop import investigate  # noqa: E402
from tools.schema_introspector import clear_schema_cache  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eval_harness")

BENCHMARK_PATH = ROOT / "eval" / "benchmark_questions.json"
RESULTS_DIR = ROOT / "eval" / "results"
REPORT_PATH = RESULTS_DIR / "report.md"
RAW_PATH = RESULTS_DIR / "latest_run.json"

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
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise TypeError("benchmark_questions.json must be a JSON array")
    return data


def extract_final_answer(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "ok":
        return str(result.get("answer") or "").strip()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run benchmark eval harness")
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
    args = parser.parse_args(argv)

    questions = load_benchmark()
    if args.ids.strip():
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    client = get_llm_provider()
    rows: list[dict[str, Any]] = []
    for q in questions:
        row = run_one(q, client=client)
        rows.append(row)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        RAW_PATH.write_text(json.dumps(rows, indent=2, default=str))
        logger.info(
            "%s => %s (%s)",
            row["id"],
            "PASS" if row["passed"] else "FAIL",
            row.get("failure_category"),
        )

    write_report(rows)
    passed = sum(1 for r in rows if r["passed"])
    print(f"Score: {passed}/{len(rows)}")
    print(f"Report: {REPORT_PATH}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
