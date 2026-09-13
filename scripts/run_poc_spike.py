#!/usr/bin/env python3
"""Throwaway POC spike runner — three questions against the sample DB.

Usage (from repo root, with .env + docker db up):
  .venv/bin/python scripts/run_poc_spike.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from orchestrator.poc import run_poc  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

QUESTIONS = [
    {
        "id": "Q1_one_step",
        "label": "One-step",
        "question": "How many orders are in the database?",
        "notes": "Should resolve with a single COUNT(*) on orders.",
    },
    {
        "id": "Q2_multi_step",
        "label": "Multi-step (2+ queries)",
        "question": (
            "Which region had the largest month-over-month order growth "
            "(by order count) in the sample data?"
        ),
        "notes": "Needs join customers↔orders plus a MoM comparison — not one trivial SELECT.",
    },
    {
        "id": "Q3_trap",
        "label": "Ambiguous / trap",
        "question": (
            "What is the total revenue (sum of line_total) for Apparel products "
            "sold through the store channel?"
        ),
        "notes": (
            "Trap: easy to join products→orders on the wrong key or sum products.unit_price "
            "without order_items, or to drop the channel filter."
        ),
    },
]


def main() -> int:
    results: list[dict] = []
    for item in QUESTIONS:
        print("\n" + "=" * 72)
        print(f"{item['id']}: {item['question']}")
        print("=" * 72)
        try:
            outcome = run_poc(item["question"])
        except Exception as exc:  # noqa: BLE001
            outcome = {
                "status": "error",
                "question": item["question"],
                "answer": None,
                "message": str(exc),
                "iterations": 0,
                "trace": [],
                "verification": None,
            }
        record = {
            **item,
            "outcome": {
                "status": outcome.get("status"),
                "answer": outcome.get("answer"),
                "iterations": outcome.get("iterations"),
                "sql_steps": outcome.get("sql_steps"),
                "message": outcome.get("message"),
                "verification": outcome.get("verification"),
                # Keep a short trace for the DECISIONS log (not full row dumps).
                "trace_summary": [
                    {
                        "iteration": t.get("iteration"),
                        "action": t.get("action"),
                        "query": (
                            (t.get("arguments") or {}).get("query")
                            if t.get("action") == "run_sql"
                            else None
                        ),
                        "error": (
                            (t.get("observation") or {}).get("error")
                            if isinstance(t.get("observation"), dict)
                            else None
                        ),
                    }
                    for t in outcome.get("trace") or []
                ],
            },
        }
        results.append(record)
        print(json.dumps(record["outcome"], indent=2, default=str)[:4000])

    out_dir = ROOT / "logs"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"poc_spike_{stamp}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out_path}")
    # Also write a stable path for the DECISIONS.md authoring step.
    latest = out_dir / "poc_spike_latest.json"
    latest.write_text(json.dumps(results, indent=2, default=str))
    print(f"Wrote {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
