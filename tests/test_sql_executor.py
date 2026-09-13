"""Tests for the SQL executor guardrails (docs/API.md run_sql contract)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import errors as pg_errors

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools.sql_executor import SqlValidationError, run_sql  # noqa: E402

load_dotenv(ROOT / ".env")


@pytest.fixture(autouse=True)
def _require_readonly_db() -> None:
    if not os.getenv("READONLY_DATABASE_URL"):
        pytest.fail("READONLY_DATABASE_URL must be set (see .env.example).")


def test_normal_select_succeeds() -> None:
    result = run_sql("SELECT order_id, channel FROM orders ORDER BY order_id LIMIT 5")
    assert result["row_count"] == 5
    assert result["truncated"] is False
    assert result["columns"] == ["order_id", "channel"]
    assert len(result["rows"]) == 5
    assert result["execution_time_seconds"] >= 0


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO orders (customer_id, order_date, channel) VALUES (1, CURRENT_DATE, 'web')",
        "UPDATE orders SET channel = 'x' WHERE order_id = 1",
        "DELETE FROM orders WHERE order_id = 1",
        "DROP TABLE orders",
    ],
)
def test_non_select_rejected_pre_execution(
    query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forbidden statements must fail in validation — never reach Postgres."""

    def _boom(*_args, **_kwargs):  # pragma: no cover - should never run
        raise AssertionError("psycopg.connect must not be called for invalid SQL")

    monkeypatch.setattr(psycopg, "connect", _boom)
    with pytest.raises(
        SqlValidationError, match="(?i)select|forbidden|allowed|rejected"
    ):
        run_sql(query)


def test_statement_timeout_cancels_long_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "2")
    started = time.perf_counter()
    with pytest.raises(pg_errors.QueryCanceled):
        run_sql("SELECT pg_sleep(30)")
    elapsed = time.perf_counter() - started
    # Should abort near the configured timeout, not after the full sleep.
    assert elapsed < 8, f"timeout took too long: {elapsed:.2f}s"
    assert elapsed >= 1.5, f"timeout fired too early: {elapsed:.2f}s"


def test_row_limit_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROW_LIMIT", "50")
    result = run_sql("SELECT generate_series(1, 200) AS n")
    assert result["truncated"] is True
    assert result["row_count"] == 50
    assert len(result["rows"]) == 50
    assert result["rows"][0]["n"] == 1
    assert result["rows"][-1]["n"] == 50
