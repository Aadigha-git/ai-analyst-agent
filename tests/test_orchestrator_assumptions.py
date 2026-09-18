"""Regression: glossary defaults must be disclosed in the narrative (v2-2 / BR-12)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cli import present_investigation  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from orchestrator.loop import investigate  # noqa: E402


def _tool(name: str, arguments: dict | None = None) -> LLMResponse:
    return LLMResponse(
        type="tool_call",
        tool_name=name,
        tool_args=arguments or {},
        tokens_used=2,
    )


def test_electronics_revenue_without_date_discloses_assumption(
    monkeypatch,
) -> None:
    """BQ-11-style trap: answering a silent all-time total must name the default."""
    question = "What is total Electronics revenue?"
    client = MagicMock()
    # Plan: SQL → ready_to_answer; format_output narrative call.
    client.chat.side_effect = [
        _tool(
            "run_sql",
            {
                "query": (
                    "SELECT SUM(oi.line_total) AS revenue "
                    "FROM order_items oi "
                    "JOIN products p ON oi.product_id = p.product_id "
                    "WHERE p.category = 'Electronics'"
                )
            },
        ),
        _tool(
            "ready_to_answer",
            {"answer": "The total Electronics revenue is $157,640.18."},
        ),
        LLMResponse(
            type="text",
            text="The total Electronics revenue is $157,640.18.",
            tokens_used=3,
        ),
    ]

    monkeypatch.setattr(
        "orchestrator.loop.run_sql",
        lambda query: {
            "rows": [{"revenue": 157640.18}],
            "row_count": 1,
            "truncated": False,
            "columns": ["revenue"],
            "execution_time_seconds": 0.01,
        },
    )
    monkeypatch.setattr(
        "orchestrator.loop.verify",
        lambda *args, **kwargs: {
            "consistent": True,
            "detail": "matches",
            "new_query_used": "SELECT SUM(line_total) FROM order_items",
        },
    )
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)

    fake_schema = {
        "tables": [
            {"schema": "public", "name": "order_items"},
            {"schema": "public", "name": "products"},
        ],
        "columns": [],
        "foreign_keys": [],
    }

    result = investigate(
        question,
        client=client,
        max_iterations=8,
        schema_loader=lambda: fake_schema,
        run_verification=True,
    )

    assert result["status"] == "ok"
    assert result["defaults_used"], "expected glossary defaults to be recorded"
    assert any("all available dates" in d for d in result["defaults_used"])
    assert any("revenue" in d.lower() for d in result["defaults_used"])

    rendered = present_investigation(result, verbose=False, client=client)
    assert "Assumption:" in rendered
    assert "all available dates" in rendered.lower()
    assert "time range" in rendered.lower()
    # Must not be a bare numeric dump without disclosure.
    assert "157" in rendered or "Electronics" in rendered
