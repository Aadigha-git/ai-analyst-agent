"""Unit tests for NebiusClient (mocked HTTP — no real API key required)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import RUN_SQL_TOOL_SCHEMA, NebiusClient  # noqa: E402


def _mock_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


def test_complete_returns_tool_call_and_logs_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": json.dumps({"query": "SELECT 1 AS n"}),
                                },
                            }
                        ]
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }
    )

    client = NebiusClient(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        session=session,
    )

    with caplog.at_level(logging.INFO, logger="llm_client"):
        result = client.complete(
            system_prompt="You are a helpful analyst.",
            messages=[{"role": "user", "content": "How many orders?"}],
            tools=[RUN_SQL_TOOL_SCHEMA],
        )

    assert result.kind == "tool_call"
    assert result.tool_call is not None
    assert result.tool_call.name == "run_sql"
    assert result.tool_call.arguments == {"query": "SELECT 1 AS n"}
    assert result.usage["total_tokens"] == 18
    assert "nebius_token_usage" in caplog.text
    assert "prompt_tokens=11" in caplog.text

    kwargs = session.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["model"] == "test-model"
    assert kwargs["json"]["messages"][0]["role"] == "system"
    assert kwargs["json"]["tools"] == [RUN_SQL_TOOL_SCHEMA]


def test_complete_returns_final_text() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        {
            "choices": [{"message": {"content": "I need a clarifying question."}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    client = NebiusClient(
        api_key="k", base_url="https://example.test/v1", session=session
    )
    result = client.complete(
        system_prompt="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
    )
    assert result.kind == "text"
    assert result.text == "I need a clarifying question."
    assert result.usage["total_tokens"] == 5


def test_missing_api_key_raises() -> None:
    client = NebiusClient(api_key="", base_url="https://example.test/v1")
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        client.complete(system_prompt="s", messages=[{"role": "user", "content": "q"}])
