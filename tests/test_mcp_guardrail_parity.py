"""MCP path must honor the same write-guardrails as the CLI (v2-10 / BR-18).

Reuses the adversarial INSERT/UPDATE/DELETE/DROP cases from
``tests/test_sql_executor.py`` and ``tests/test_db_role.py``, but drives them
through ``ask_data_question`` (and the shared orchestrator) via the *question*
text — not a direct ``run_sql`` call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import LLMResponse  # noqa: E402
from mcp_server import ask_data_question  # noqa: E402
from orchestrator.loop import investigate  # noqa: E402
from output_formatter import format_output  # noqa: E402
from tools.sql_executor import SqlValidationError, run_sql  # noqa: E402

load_dotenv(ROOT / ".env")

# Same destructive statements as test_sql_executor / test_db_role.
ADVERSARIAL_SQL = [
    (
        "INSERT",
        "INSERT INTO orders (customer_id, order_date, channel) "
        "VALUES (1, CURRENT_DATE, 'web')",
    ),
    ("UPDATE", "UPDATE orders SET channel = 'x' WHERE order_id = 1"),
    ("DELETE", "DELETE FROM orders WHERE order_id = 1"),
    ("DROP", "DROP TABLE orders"),
]

_DECLINE_HINTS = (
    "cannot",
    "can't",
    "unable",
    "not allowed",
    "forbidden",
    "rejected",
    "read-only",
    "readonly",
    "decline",
    "refus",
    "not permit",
    "won't",
    "will not",
    "destructive",
    "write",
    "insert",
    "update",
    "delete",
    "drop",
    "select-only",
    "select only",
)


def _tool(name: str, arguments: dict | None = None) -> LLMResponse:
    return LLMResponse(
        type="tool_call",
        tool_name=name,
        tool_args=arguments or {},
        tokens_used=2,
    )


def _text(content: str) -> LLMResponse:
    return LLMResponse(type="text", text=content, tokens_used=2)


def _decline_answer(verb: str) -> str:
    return (
        f"I cannot perform a {verb} — this agent only runs read-only SELECT "
        f"queries. The requested write/destructive statement was rejected by "
        f"SQL validation before reaching the database."
    )


def _assert_explains_decline(narrative: str) -> None:
    lowered = narrative.lower()
    assert any(h in lowered for h in _DECLINE_HINTS), narrative
    assert "successfully deleted" not in lowered
    assert "successfully dropped" not in lowered
    assert "rows inserted" not in lowered
    assert "table dropped" not in lowered


@pytest.fixture
def fake_schema() -> dict:
    return {
        "tables": [{"schema": "public", "name": "orders"}],
        "columns": [],
        "foreign_keys": [],
    }


@pytest.fixture
def database_url() -> str:
    return os.getenv(
        "READONLY_DATABASE_URL",
        "postgresql://analyst_readonly:change_me@localhost:5432/analyst",
    )


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_schema: dict,
) -> None:
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)
    monkeypatch.setattr("orchestrator.loop.introspect_schema", lambda: fake_schema)
    monkeypatch.setattr("mcp_server.clear_schema_cache", lambda: None)
    monkeypatch.setattr(
        "orchestrator.loop.verify",
        lambda *a, **k: {
            "consistent": True,
            "detail": "guardrail-parity stub",
            "new_query_used": "",
        },
    )

    def _boom(*_a, **_k):  # pragma: no cover - must never run for invalid SQL
        raise AssertionError("psycopg.connect must not be called for invalid SQL")

    monkeypatch.setattr(psycopg, "connect", _boom)


def _adversarial_client(sql: str, decline: str) -> MagicMock:
    client = MagicMock()
    client.chat.side_effect = [
        # Adversarial model tries to honor the write instruction.
        _tool("run_sql", {"query": sql}),
        # After validation error in evidence, explain the limitation (BR-7 spirit).
        _tool("ready_to_answer", {"answer": decline}),
        _text(decline),
    ]
    return client


@pytest.mark.parametrize(("verb", "sql"), ADVERSARIAL_SQL)
def test_mcp_ask_rejects_destructive_sql_like_cli(
    verb: str,
    sql: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_schema: dict,
    database_url: str,
) -> None:
    """Destructive instruction via the question → no write; narrative explains."""
    _patch_common(monkeypatch, fake_schema=fake_schema)

    # Same pre-execution rejection as test_sql_executor (CLI guardrail).
    with pytest.raises(SqlValidationError):
        run_sql(sql)

    question = (
        f"Please execute this SQL against the database and confirm it worked: {sql}"
    )
    decline = _decline_answer(verb)

    mcp_narrative = ask_data_question(
        question, database_url, client=_adversarial_client(sql, decline)
    )

    result = investigate(
        question,
        client=_adversarial_client(sql, decline),
        schema_loader=lambda: fake_schema,
        run_verification=True,
    )
    assert result["status"] == "ok"
    evidence_blob = str(getattr(result.get("state"), "evidence", []) or []).lower()
    assert (
        "error" in evidence_blob
        or "forbidden" in evidence_blob
        or "select" in evidence_blob
        or "rejected" in evidence_blob
    )

    fmt_client = MagicMock()
    fmt_client.chat.side_effect = [_text(decline)]
    cli_narrative = str(
        format_output(
            result.get("answer") or decline,
            list(getattr(result.get("state"), "evidence", []) or []),
            client=fmt_client,
            defaults_used=list(
                getattr(result.get("state"), "defaults_used", None) or []
            ),
        )["narrative"]
    )

    _assert_explains_decline(mcp_narrative)
    _assert_explains_decline(cli_narrative)
    # Outcomes match: both surfaces explain the limitation (not a silent ignore).
    assert mcp_narrative.strip() == cli_narrative.strip()


@pytest.mark.parametrize(("verb", "sql"), ADVERSARIAL_SQL)
def test_mcp_destructive_attempt_never_opens_db(
    verb: str,
    sql: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_schema: dict,
    database_url: str,
) -> None:
    """Parity with test_sql_executor: invalid SQL never reaches Postgres."""
    _patch_common(monkeypatch, fake_schema=fake_schema)
    decline = _decline_answer(verb)

    narrative = ask_data_question(
        f"Run this destructive statement now: {sql}",
        database_url,
        client=_adversarial_client(sql, decline),
    )
    _assert_explains_decline(narrative)


def test_mcp_verification_path_invokes_verify(
    monkeypatch: pytest.MonkeyPatch,
    fake_schema: dict,
    database_url: str,
) -> None:
    """A normal factual question through MCP must still call verify() (not skip)."""
    monkeypatch.setattr("orchestrator.loop.clear_schema_cache", lambda: None)
    monkeypatch.setattr("orchestrator.loop.introspect_schema", lambda: fake_schema)
    monkeypatch.setattr("mcp_server.clear_schema_cache", lambda: None)
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

    verify_spy = MagicMock(
        return_value={
            "consistent": True,
            "detail": "matches independent count",
            "new_query_used": "SELECT COUNT(*) FROM orders",
        }
    )
    monkeypatch.setattr("orchestrator.loop.verify", verify_spy)

    answer = "There are 200 orders in the database."
    client = MagicMock()
    client.chat.side_effect = [
        _tool("run_sql", {"query": "SELECT COUNT(*) AS n FROM orders"}),
        _tool("ready_to_answer", {"answer": answer}),
        _text(answer),
    ]

    narrative = ask_data_question(
        "How many orders are in the database?",
        database_url,
        client=client,
    )

    assert verify_spy.call_count == 1
    # verify(claim, evidence_ref, ...) — first positional arg is the draft claim.
    draft = verify_spy.call_args.args[0]
    assert "200" in str(draft)
    assert "200" in narrative
