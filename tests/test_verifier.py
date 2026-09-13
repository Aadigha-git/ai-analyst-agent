"""Tests for production verify() (ADR-005)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import LlmResult  # noqa: E402
from tools.verifier import DuplicateVerificationQuery, verify  # noqa: E402


def _text(content: str) -> LlmResult:
    return LlmResult(
        kind="text",
        text=content,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


def test_wrong_claim_marked_inconsistent() -> None:
    """Different verification query whose result contradicts a wrong draft claim."""
    original = "SELECT COUNT(*) AS n FROM orders"
    evidence = json.dumps(
        [
            {
                "action": "run_sql",
                "result": {
                    "query": original,
                    "rows": [{"n": 200}],
                    "row_count": 1,
                },
            }
        ]
    )
    # Deliberately wrong claim (seed has 200 orders).
    claim = "There are exactly 50 orders in the database."

    client = MagicMock()
    client.complete.side_effect = [
        # Genuinely different corroborating query (not a re-run of COUNT(*)).
        _text(
            "SELECT COUNT(order_id) AS order_count FROM orders WHERE order_id IS NOT NULL"
        ),
        _text(
            json.dumps(
                {
                    "consistent": False,
                    "detail": "Verification count is 200, which contradicts the claim of 50.",
                }
            )
        ),
    ]

    def fake_sql(query: str) -> dict:
        assert query != original
        return {
            "rows": [{"order_count": 200}],
            "row_count": 1,
            "truncated": False,
            "columns": ["order_count"],
        }

    result = verify(
        claim,
        evidence,
        client=client,
        prior_queries=[original],
        sql_runner=fake_sql,
    )

    assert result["consistent"] is False
    assert "200" in result["detail"] or "contradict" in result["detail"].lower()
    assert result["new_query_used"]
    assert result["new_query_used"].strip().rstrip(";") != original.strip().rstrip(";")
    assert client.complete.call_count == 2


def test_correct_claim_marked_consistent() -> None:
    """Different verification query that corroborates a correct draft claim."""
    original = "SELECT COUNT(*) AS n FROM orders"
    evidence = json.dumps(
        [
            {
                "action": "run_sql",
                "result": {"query": original, "rows": [{"n": 200}], "row_count": 1},
            }
        ]
    )
    claim = "There are 200 orders in the database."

    client = MagicMock()
    client.complete.side_effect = [
        _text("SELECT COUNT(1) AS total_orders FROM orders"),
        _text(
            json.dumps(
                {
                    "consistent": True,
                    "detail": "Independent COUNT(1) also returns 200, matching the claim.",
                }
            )
        ),
    ]

    def fake_sql(query: str) -> dict:
        return {
            "rows": [{"total_orders": 200}],
            "row_count": 1,
            "truncated": False,
            "columns": ["total_orders"],
        }

    result = verify(
        claim,
        evidence,
        client=client,
        prior_queries=[original],
        sql_runner=fake_sql,
    )

    assert result["consistent"] is True
    assert "200" in result["detail"]
    assert "COUNT(1)" in result["new_query_used"].upper().replace(" ", "")
    assert client.complete.call_count == 2


def test_identical_verification_query_raises() -> None:
    """ADR-005: identical SQL must raise, not silently re-run."""
    original = "SELECT COUNT(*) FROM orders"
    client = MagicMock()
    client.complete.return_value = _text("SELECT COUNT(*) FROM orders")

    with pytest.raises(DuplicateVerificationQuery, match="must differ"):
        verify(
            "There are 200 orders.",
            json.dumps([{"result": {"query": original}}]),
            client=client,
            prior_queries=[original],
            sql_runner=lambda q: {"rows": []},
        )
