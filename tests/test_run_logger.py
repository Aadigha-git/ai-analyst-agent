"""Tests for structured redacted run logging (v2-5 / BR-15, BR-17)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cli import app  # noqa: E402
from run_logger import (  # noqa: E402
    RunLogger,
    estimate_cost_usd,
    redact_payload,
)

REQUIRED_KEYS = {
    "step_type",
    "timestamp",
    "latency_ms",
    "tokens_used",
    "cost_estimate_usd",
    "payload",
}


def test_session_writes_valid_jsonl_with_redacted_rows(tmp_path: Path) -> None:
    session = RunLogger(
        redact=True,
        log_dir=tmp_path,
        provider="nebius",
        model="meta-llama/Llama-3.3-70B-Instruct",
    )
    assert session.path.name.startswith("run_")
    assert session.path.suffix == ".jsonl"

    session.record(
        "plan",
        latency_ms=12,
        tokens_used=1000,
        payload={"iteration": 1, "tool_name": "run_sql"},
    )
    session.record(
        "tool_call",
        tokens_used=1000,
        payload={"tool": "run_sql", "arguments": {"query": "SELECT 1"}},
    )
    session.record(
        "observe",
        latency_ms=5,
        payload={
            "tool": "run_sql",
            "observation": {
                "query": "SELECT region, n FROM t",
                "row_count": 2,
                "columns": ["region", "n"],
                "rows": [{"region": "East", "n": 50}, {"region": "West", "n": 34}],
            },
        },
    )
    session.record(
        "verify",
        latency_ms=20,
        tokens_used=200,
        payload={"verification": {"consistent": True, "detail": "ok"}},
    )
    session.close()

    lines = [
        ln for ln in session.path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == 4
    events = [json.loads(ln) for ln in lines]
    assert [e["step_type"] for e in events] == [
        "plan",
        "tool_call",
        "observe",
        "verify",
    ]
    for event in events:
        assert REQUIRED_KEYS <= set(event)
        assert isinstance(event["latency_ms"], int)
        assert isinstance(event["tokens_used"], int)
        assert isinstance(event["cost_estimate_usd"], float)

    observe = events[2]["payload"]["observation"]
    assert observe["rows"] == {
        "_redacted": True,
        "row_count": 2,
        "columns": ["region", "n"],
    }
    assert "East" not in json.dumps(observe)
    assert events[0]["cost_estimate_usd"] == estimate_cost_usd(
        1000, provider="nebius", model="meta-llama/Llama-3.3-70B-Instruct"
    )


def test_no_redact_reveals_row_values(tmp_path: Path) -> None:
    session = RunLogger(redact=False, log_dir=tmp_path, path=tmp_path / "debug.jsonl")
    rows = [{"region": "Central", "n": 37}]
    session.record(
        "observe",
        payload={
            "observation": {
                "row_count": 1,
                "columns": ["region", "n"],
                "rows": rows,
            }
        },
    )
    event = session.read_events()[0]
    assert event["payload"]["observation"]["rows"] == rows
    assert event["payload"]["observation"]["rows"][0]["region"] == "Central"


def test_cli_no_redact_flag_disables_redaction(monkeypatch, tmp_path: Path) -> None:
    """``ask --no-redact`` must construct RunLogger(redact=False)."""
    captured: dict[str, object] = {}

    class _FakeLLM:
        provider_id = "nebius"
        model = "meta-llama/Llama-3.3-70B-Instruct"

    def fake_get_provider():
        return _FakeLLM()

    def fake_investigate(question, *, client=None, run_logger=None, **kwargs):
        captured["run_logger"] = run_logger
        captured["question"] = question
        return {
            "status": "needs_clarification",
            "clarifying_question": "Which metric?",
            "reason": "test",
        }

    def fake_present(*args, **kwargs):
        return ""

    monkeypatch.setattr("cli.get_llm_provider", fake_get_provider)
    monkeypatch.setattr("cli.print_cli_banner", lambda *a, **k: None)
    monkeypatch.setattr("cli.investigate", fake_investigate)
    monkeypatch.setattr("cli.present_investigation", fake_present)

    # Force log files into tmp_path.
    monkeypatch.setattr(
        "cli.RunLogger",
        lambda **kwargs: RunLogger(log_dir=tmp_path, **kwargs),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["ask", "How are sales?", "--no-redact"])
    assert result.exit_code == 0, result.output
    logger = captured["run_logger"]
    assert isinstance(logger, RunLogger)
    assert logger.redact is False

    # Default (no flag) keeps redaction on.
    captured.clear()
    result = runner.invoke(app, ["ask", "How many orders?"])
    assert result.exit_code == 0, result.output
    logger = captured["run_logger"]
    assert isinstance(logger, RunLogger)
    assert logger.redact is True


def test_redact_payload_helper_preserves_non_row_fields() -> None:
    payload = {
        "query": "SELECT * FROM orders",
        "truncated": False,
        "rows": [{"order_id": 1}],
        "columns": ["order_id"],
    }
    redacted = redact_payload(payload)
    assert redacted["query"] == "SELECT * FROM orders"
    assert redacted["rows"]["_redacted"] is True
    assert redacted["rows"]["row_count"] == 1
