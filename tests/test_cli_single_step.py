"""CLI single-step path with a mocked LLM tool call (real run_sql when DB is up)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from cli import app, run_single_step  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from tools.schema_introspector import clear_schema_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_schema() -> None:
    clear_schema_cache()


@pytest.fixture
def readonly_configured() -> None:
    if not os.getenv("READONLY_DATABASE_URL"):
        pytest.fail("READONLY_DATABASE_URL must be set (see .env.example).")


def test_run_single_step_executes_mocked_run_sql_tool_call(
    readonly_configured: None,
) -> None:
    client = MagicMock()
    client.chat.return_value = LLMResponse(
        type="tool_call",
        tool_name="run_sql",
        tool_args={"query": "SELECT COUNT(*) AS order_count FROM orders"},
        tokens_used=2,
    )

    payload = run_single_step("How many orders are there?", client=client)

    assert payload["type"] == "tool_result"
    assert payload["tool"] == "run_sql"
    assert "COUNT(*)" in payload["query"].upper()
    assert payload["result"]["row_count"] == 1
    assert payload["result"]["rows"][0]["order_count"] == 200
    assert payload["llm"]["usage"]["total_tokens"] == 2

    client.chat.assert_called_once()
    kwargs = client.chat.call_args.kwargs
    assert "tools" in kwargs
    assert kwargs["tools"][0]["function"]["name"] == "run_sql"
    user_msg = kwargs["messages"][0]["content"]
    assert "Question: How many orders are there?" in user_msg
    assert "orders" in user_msg.lower()


def test_cli_ask_command_prints_narrative(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_result = {
        "status": "ok",
        "answer": "There are 200 orders.",
        "trace": [],
        "state": MagicMock(
            evidence=[
                {
                    "action": "run_sql",
                    "result": {
                        "query": "SELECT COUNT(*) AS n FROM orders",
                        "columns": ["n"],
                        "rows": [{"n": 200}],
                    },
                }
            ]
        ),
        "verification": {"consistent": True},
    }
    client = MagicMock()
    client.provider_id = "nebius"
    client.model = "test-model"
    client.chat.return_value = LLMResponse(
        type="text",
        text="There are 200 orders in the database.",
        tokens_used=2,
    )
    monkeypatch.setattr("cli.get_llm_provider", lambda: client)
    monkeypatch.setattr(
        "cli.investigate",
        lambda question, client=None, run_logger=None, **kwargs: fake_result,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["ask", "ping"])
    assert result.exit_code == 0, result.output
    assert "200" in result.output
    assert "SELECT" not in result.output.upper()
