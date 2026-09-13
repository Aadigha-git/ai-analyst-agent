"""Tests for narrative output formatter and CLI --verbose flag."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cli import app, present_investigation  # noqa: E402
from llm_client import LlmResult  # noqa: E402
from output_formatter import format_output, render_formatted  # noqa: E402


def _narrative(text: str) -> LlmResult:
    return LlmResult(
        kind="text",
        text=text,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


EVIDENCE = [
    {
        "action": "run_sql",
        "result": {
            "query": "SELECT COUNT(*) AS order_count FROM orders",
            "columns": ["order_count"],
            "rows": [{"order_count": 200}],
            "row_count": 1,
        },
    }
]


def test_default_format_output_contains_no_raw_sql() -> None:
    client = MagicMock()
    client.complete.return_value = _narrative(
        "There are 200 orders in the database based on the verified count."
    )

    formatted = format_output(
        "There are 200 orders.",
        EVIDENCE,
        client=client,
        verbose=False,
    )
    rendered = render_formatted(formatted, verbose=False)

    assert "200" in formatted["narrative"]
    assert "sql_queries" not in formatted
    assert "trace" not in formatted
    assert "SELECT" not in formatted["narrative"].upper()
    assert "SELECT" not in rendered.upper()
    assert "FROM ORDERS" not in rendered.upper()
    assert formatted["table"]["rows"][0]["order_count"] == 200


def test_verbose_format_output_includes_sql_and_trace() -> None:
    client = MagicMock()
    client.complete.return_value = _narrative("There are 200 orders in the database.")
    trace = [
        {
            "iteration": 1,
            "action": "run_sql",
            "arguments": {"query": "SELECT COUNT(*) AS order_count FROM orders"},
        }
    ]

    formatted = format_output(
        "There are 200 orders.",
        EVIDENCE,
        client=client,
        verbose=True,
        trace=trace,
    )
    rendered = render_formatted(formatted, verbose=True)

    assert any("SELECT COUNT(*)" in q.upper() for q in formatted["sql_queries"])
    assert formatted["trace"] == trace
    assert "SELECT" in rendered.upper()
    assert "COUNT(*)" in rendered.upper()
    assert "tool trace" in rendered.lower()


def test_cli_verbose_flag_controls_sql_visibility(monkeypatch) -> None:
    fake_result = {
        "status": "ok",
        "answer": "There are 200 orders.",
        "trace": [
            {
                "iteration": 1,
                "action": "run_sql",
                "arguments": {"query": "SELECT COUNT(*) AS order_count FROM orders"},
            }
        ],
        "state": MagicMock(evidence=EVIDENCE),
        "verification": {"consistent": True},
    }
    client = MagicMock()
    client.complete.return_value = _narrative("There are 200 orders in total.")

    text_default = present_investigation(fake_result, verbose=False, client=client)
    assert "SELECT" not in text_default.upper()
    assert "200" in text_default

    text_verbose = present_investigation(fake_result, verbose=True, client=client)
    assert "SELECT" in text_verbose.upper()
    assert "COUNT(*)" in text_verbose.upper()

    monkeypatch.setattr("cli.investigate", lambda question: fake_result)
    monkeypatch.setattr(
        "cli.present_investigation",
        lambda result, verbose=False, client=None, console_=None: (
            present_investigation(result, verbose=verbose, client=client)
        ),
    )
    # Ensure ask → present uses mocked LLM via patching format_output's client factory
    monkeypatch.setattr("output_formatter.NebiusClient", lambda: client)

    runner = CliRunner()
    default = runner.invoke(app, ["ask", "how many orders?"])
    assert default.exit_code == 0, default.output
    assert "SELECT" not in default.output.upper()

    verbose = runner.invoke(app, ["ask", "how many orders?", "--verbose"])
    assert verbose.exit_code == 0, verbose.output
    assert "SELECT" in verbose.output.upper()
