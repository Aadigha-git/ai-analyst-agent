"""Tests for the production plan/execute/reflect loop."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import LlmResult, ToolCall  # noqa: E402
from orchestrator.loop import InvestigationState, investigate  # noqa: E402


def _tool(name: str, arguments: dict | None = None, call_id: str = "c1") -> LlmResult:
    return LlmResult(
        kind="tool_call",
        tool_call=ToolCall(id=call_id, name=name, arguments=arguments or {}),
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


@pytest.fixture
def fake_schema() -> dict:
    return {
        "tables": [{"schema": "public", "name": "orders"}],
        "columns": [
            {
                "schema": "public",
                "table": "orders",
                "name": "order_id",
                "data_type": "integer",
                "is_nullable": "NO",
            }
        ],
        "foreign_keys": [],
    }


def test_multi_step_question_completes_within_cap(
    monkeypatch: pytest.MonkeyPatch, fake_schema: dict
) -> None:
    """Normal path: run_sql then ready_to_answer within MAX_ITERATIONS."""
    client = MagicMock()
    client.complete.side_effect = [
        _tool("run_sql", {"query": "SELECT COUNT(*) AS n FROM orders"}, "c1"),
        _tool("ready_to_answer", {"answer": "There are 200 orders."}, "c2"),
    ]

    monkeypatch.setattr(
        "orchestrator.loop.run_sql",
        lambda query: {
            "rows": [{"n": 200}],
            "row_count": 1,
            "truncated": False,
            "columns": ["n"],
            "execution_time_seconds": 0.01,
        },
    )
    monkeypatch.setattr(
        "orchestrator.loop.verify",
        lambda *args, **kwargs: {
            "consistent": True,
            "detail": "matches",
            "new_query_used": "SELECT 200",
        },
    )
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)

    result = investigate(
        "How many orders?",
        client=client,
        max_iterations=8,
        schema_loader=lambda: fake_schema,
        run_verification=True,
    )

    assert result["status"] == "ok"
    assert result["answer"] == "There are 200 orders."
    assert result["iterations"] == 2
    assert result["sql_steps"] == 1
    assert isinstance(result["state"], InvestigationState)
    assert client.complete.call_count == 2


def test_never_ready_returns_uncertain_not_infinite(
    monkeypatch: pytest.MonkeyPatch, fake_schema: dict
) -> None:
    """Mocked LLM that never says ready must stop at MAX_ITERATIONS."""
    cap = 3
    client = MagicMock()
    client.complete.side_effect = [
        _tool("run_sql", {"query": f"SELECT {i}"}, f"c{i}") for i in range(cap + 5)
    ]

    monkeypatch.setattr(
        "orchestrator.loop.run_sql",
        lambda query: {
            "rows": [{"x": 1}],
            "row_count": 1,
            "truncated": False,
            "columns": ["x"],
            "execution_time_seconds": 0.0,
        },
    )
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)
    # verify must not be called when never ready
    verify_mock = MagicMock()
    monkeypatch.setattr("orchestrator.loop.verify", verify_mock)

    result = investigate(
        "Keep investigating forever",
        client=client,
        max_iterations=cap,
        schema_loader=lambda: fake_schema,
    )

    assert result["status"] == "uncertain"
    assert result["answer"] is None
    assert result["iterations"] == cap
    assert "iteration cap" in (result.get("message") or "").lower()
    assert client.complete.call_count == cap
    verify_mock.assert_not_called()


def test_ambiguous_plan_triggers_clarification_branch(
    monkeypatch: pytest.MonkeyPatch, fake_schema: dict
) -> None:
    """NEEDS_CLARIFICATION stops the loop and returns a clarifying question."""
    client = MagicMock()
    client.complete.return_value = _tool(
        "needs_clarification",
        {
            "clarifying_question": "Which metric should I use — order count or revenue?",
            "reason": "The question does not specify the metric.",
        },
    )
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)
    run_sql_mock = MagicMock()
    monkeypatch.setattr("orchestrator.loop.run_sql", run_sql_mock)

    result = investigate(
        "How did sales do recently?",
        client=client,
        max_iterations=8,
        schema_loader=lambda: fake_schema,
    )

    assert result["status"] == "needs_clarification"
    assert "metric" in result["clarifying_question"].lower()
    assert result["iterations"] == 1
    assert result["reason"]
    run_sql_mock.assert_not_called()
    assert client.complete.call_count == 1
