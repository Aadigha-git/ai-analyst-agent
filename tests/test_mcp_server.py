"""Tests for the MCP ask_data_question entrypoint (v2-9 / BR-18)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import LLMResponse  # noqa: E402
from mcp_server import ask_data_question  # noqa: E402
from orchestrator.loop import investigate  # noqa: E402
from output_formatter import format_output  # noqa: E402


def _tool(name: str, arguments: dict | None = None) -> LLMResponse:
    return LLMResponse(
        type="tool_call",
        tool_name=name,
        tool_args=arguments or {},
        tokens_used=2,
    )


def test_ask_data_question_returns_cli_equivalent_narrative(monkeypatch) -> None:
    """Underlying function (no MCP transport) matches CLI format_output narrative."""
    question = "How many orders are in the database?"
    database_url = "postgresql://analyst_readonly:change_me@localhost:5432/analyst"

    fake_schema = {
        "tables": [{"schema": "public", "name": "orders"}],
        "columns": [],
        "foreign_keys": [],
    }

    def make_client() -> MagicMock:
        client = MagicMock()
        client.chat.side_effect = [
            _tool("run_sql", {"query": "SELECT COUNT(*) AS n FROM orders"}),
            _tool(
                "ready_to_answer",
                {"answer": "There are 200 orders in the database."},
            ),
            LLMResponse(
                type="text",
                text="There are 200 orders in the database.",
                tokens_used=3,
            ),
        ]
        return client

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
            "new_query_used": "SELECT COUNT(*) FROM orders",
        },
    )
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)
    monkeypatch.setattr("orchestrator.loop.introspect_schema", lambda: fake_schema)
    monkeypatch.setattr("mcp_server.clear_schema_cache", lambda: None)

    mcp_narrative = ask_data_question(
        question,
        database_url,
        client=make_client(),
    )

    result = investigate(
        question,
        client=make_client(),
        max_iterations=8,
        schema_loader=lambda: fake_schema,
        run_verification=True,
    )
    assert result["status"] == "ok"
    state = result["state"]
    cli_client = make_client()
    # Only the format_output LLM call is needed here.
    cli_client.chat.side_effect = [
        LLMResponse(
            type="text",
            text="There are 200 orders in the database.",
            tokens_used=3,
        )
    ]
    cli_formatted = format_output(
        result["answer"],
        list(state.evidence),
        client=cli_client,
        defaults_used=list(getattr(state, "defaults_used", []) or []),
    )
    cli_narrative = str(cli_formatted["narrative"]).strip()

    assert "200" in mcp_narrative
    assert mcp_narrative == cli_narrative


def test_ask_data_question_sets_database_url(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_investigate(question, **kwargs):
        import os

        seen["readonly"] = os.environ.get("READONLY_DATABASE_URL", "")
        seen["database"] = os.environ.get("DATABASE_URL", "")
        return {
            "status": "needs_clarification",
            "clarifying_question": "Which metric?",
            "reason": "ambiguous",
        }

    monkeypatch.setattr("mcp_server.investigate", fake_investigate)
    monkeypatch.setattr("mcp_server.clear_schema_cache", lambda: None)
    monkeypatch.setattr("mcp_server.get_llm_provider", lambda: MagicMock())

    out = ask_data_question(
        "How are sales?",
        "postgresql://reader:secret@db:5432/analytics",
    )
    assert "Which metric?" in out
    assert seen["readonly"] == "postgresql://reader:secret@db:5432/analytics"
    assert seen["database"] == seen["readonly"]
