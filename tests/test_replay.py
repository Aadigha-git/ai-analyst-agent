"""Tests for the local run replay CLI (v2-6 / BR-16)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cli import app, format_replay_text, load_trace_events  # noqa: E402


def _write_fixture(path: Path) -> None:
    events = [
        {
            "step_type": "plan",
            "timestamp": "2026-09-18T00:00:01Z",
            "latency_ms": 10,
            "tokens_used": 100,
            "cost_estimate_usd": 0.000025,
            "payload": {"iteration": 1, "tool_name": "run_sql"},
        },
        {
            "step_type": "tool_call",
            "timestamp": "2026-09-18T00:00:02Z",
            "latency_ms": 0,
            "tokens_used": 100,
            "cost_estimate_usd": 0.000025,
            "payload": {
                "tool": "run_sql",
                "arguments": {"query": "SELECT COUNT(*) AS n FROM orders"},
            },
        },
        {
            "step_type": "observe",
            "timestamp": "2026-09-18T00:00:03Z",
            "latency_ms": 5,
            "tokens_used": 0,
            "cost_estimate_usd": 0.0,
            "payload": {
                "tool": "run_sql",
                "observation": {
                    "query": "SELECT COUNT(*) AS n FROM orders",
                    "row_count": 1,
                    "columns": ["n"],
                    "rows": {"_redacted": True, "row_count": 1, "columns": ["n"]},
                },
            },
        },
        {
            "step_type": "verify",
            "timestamp": "2026-09-18T00:00:04Z",
            "latency_ms": 20,
            "tokens_used": 50,
            "cost_estimate_usd": 0.0000125,
            "payload": {
                "verification": {"consistent": True, "detail": "matches"},
            },
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_replay_output_contains_step_types_and_summary(tmp_path: Path) -> None:
    trace = tmp_path / "run_fixture.jsonl"
    _write_fixture(trace)

    events = load_trace_events(trace)
    text = format_replay_text(events)

    for step_type in ("plan", "tool_call", "observe", "verify"):
        assert step_type in text
    assert "Session summary:" in text
    assert "total_latency_ms=35" in text
    assert "total_tokens=250" in text
    assert "total_cost_estimate_usd=" in text
    # Respect redaction as stored in the fixture.
    assert "_redacted" in text or "redacted rows=1" in text
    assert "200" not in text  # no invented raw cell values


def test_replay_cli_command(tmp_path: Path) -> None:
    trace = tmp_path / "run_fixture.jsonl"
    _write_fixture(trace)

    runner = CliRunner()
    result = runner.invoke(app, ["replay", str(trace)])
    assert result.exit_code == 0, result.output
    for step_type in ("plan", "tool_call", "observe", "verify"):
        assert step_type in result.output
    assert "Session summary" in result.output
    assert (
        "total_latency_ms=35" in result.output or "total_latency_ms=35" in result.stdout
    )
    assert "total_tokens=250" in result.output
