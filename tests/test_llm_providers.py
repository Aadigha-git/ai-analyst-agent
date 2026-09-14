"""Unit tests for each LLM provider — mocked SDK/HTTP responses only."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_client import (  # noqa: E402
    RUN_SQL_TOOL_SCHEMA,
    AnthropicProvider,
    GoogleProvider,
    NebiusProvider,
    OpenAIProvider,
    get_llm_provider,
)


def _nebius_http(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    session = MagicMock()
    session.post.return_value = response
    return session


def test_nebius_normalizes_tool_call_and_text() -> None:
    session = _nebius_http(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"query": "SELECT 1"}',
                                },
                            }
                        ]
                    }
                }
            ],
            "usage": {"total_tokens": 9},
        }
    )
    provider = NebiusProvider(api_key="k", session=session, model="m")
    tool = provider.chat(
        "sys", [{"role": "user", "content": "q"}], [RUN_SQL_TOOL_SCHEMA]
    )
    assert tool.type == "tool_call"
    assert tool.tool_name == "run_sql"
    assert tool.tool_args == {"query": "SELECT 1"}
    assert tool.tokens_used == 9

    session.post.return_value = _nebius_http(
        {
            "choices": [{"message": {"content": "plain text"}}],
            "usage": {"total_tokens": 3},
        }
    ).post.return_value
    text = provider.chat("sys", [{"role": "user", "content": "q"}], None)
    assert text.type == "text"
    assert text.text == "plain text"
    assert text.tokens_used == 3


def test_openai_normalizes_tool_call_and_text() -> None:
    tool_fn = SimpleNamespace(
        name="run_sql", arguments=json.dumps({"query": "SELECT 2"})
    )
    tool_call = SimpleNamespace(function=tool_fn)
    tool_message = SimpleNamespace(tool_calls=[tool_call], content=None)
    tool_completion = SimpleNamespace(
        choices=[SimpleNamespace(message=tool_message)],
        usage=SimpleNamespace(total_tokens=12, prompt_tokens=8, completion_tokens=4),
    )

    text_message = SimpleNamespace(tool_calls=None, content="hello from openai")
    text_completion = SimpleNamespace(
        choices=[SimpleNamespace(message=text_message)],
        usage=SimpleNamespace(total_tokens=4, prompt_tokens=2, completion_tokens=2),
    )

    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [tool_completion, text_completion]
    provider = OpenAIProvider(api_key="k", model="gpt-test", client=sdk)

    tool = provider.chat(
        "sys", [{"role": "user", "content": "q"}], [RUN_SQL_TOOL_SCHEMA]
    )
    assert tool.type == "tool_call"
    assert tool.tool_name == "run_sql"
    assert tool.tool_args == {"query": "SELECT 2"}
    assert tool.tokens_used == 12
    assert sdk.chat.completions.create.call_args.kwargs["tools"] == [
        RUN_SQL_TOOL_SCHEMA
    ]

    text = provider.chat("sys", [{"role": "user", "content": "q"}], None)
    assert text.type == "text"
    assert text.text == "hello from openai"
    assert text.tokens_used == 4


def test_anthropic_normalizes_tool_call_and_text() -> None:
    tool_block = SimpleNamespace(
        type="tool_use", name="run_sql", input={"query": "SELECT 3"}
    )
    tool_response = SimpleNamespace(
        content=[tool_block],
        usage=SimpleNamespace(input_tokens=5, output_tokens=7),
    )
    text_block = SimpleNamespace(type="text", text="hello from anthropic")
    text_response = SimpleNamespace(
        content=[text_block],
        usage=SimpleNamespace(input_tokens=1, output_tokens=2),
    )

    sdk = MagicMock()
    sdk.messages.create.side_effect = [tool_response, text_response]
    provider = AnthropicProvider(api_key="k", model="claude-test", client=sdk)

    tool = provider.chat(
        "sys", [{"role": "user", "content": "q"}], [RUN_SQL_TOOL_SCHEMA]
    )
    assert tool.type == "tool_call"
    assert tool.tool_name == "run_sql"
    assert tool.tool_args == {"query": "SELECT 3"}
    assert tool.tokens_used == 12
    anthropic_tools = sdk.messages.create.call_args.kwargs["tools"]
    assert anthropic_tools[0]["name"] == "run_sql"
    assert "input_schema" in anthropic_tools[0]

    text = provider.chat("sys", [{"role": "user", "content": "q"}], None)
    assert text.type == "text"
    assert text.text == "hello from anthropic"
    assert text.tokens_used == 3


def test_google_normalizes_tool_call_and_text() -> None:
    fn_call = SimpleNamespace(name="run_sql", args={"query": "SELECT 4"})
    tool_part = SimpleNamespace(function_call=fn_call, text=None)
    tool_content = SimpleNamespace(parts=[tool_part])
    tool_response = SimpleNamespace(
        candidates=[SimpleNamespace(content=tool_content)],
        usage_metadata=SimpleNamespace(total_token_count=15),
        text=None,
    )

    text_part = SimpleNamespace(function_call=None, text="hello from google")
    text_content = SimpleNamespace(parts=[text_part])
    text_response = SimpleNamespace(
        candidates=[SimpleNamespace(content=text_content)],
        usage_metadata=SimpleNamespace(total_token_count=6),
        text="hello from google",
    )

    sdk = MagicMock()
    sdk.models.generate_content.side_effect = [tool_response, text_response]
    provider = GoogleProvider(api_key="k", model="gemini-test", client=sdk)

    tool = provider.chat(
        "sys", [{"role": "user", "content": "q"}], [RUN_SQL_TOOL_SCHEMA]
    )
    assert tool.type == "tool_call"
    assert tool.tool_name == "run_sql"
    assert tool.tool_args == {"query": "SELECT 4"}
    assert tool.tokens_used == 15
    config = sdk.models.generate_content.call_args.kwargs["config"]
    assert "function_declarations" in config["tools"][0]

    text = provider.chat("sys", [{"role": "user", "content": "q"}], None)
    assert text.type == "text"
    assert text.text == "hello from google"
    assert text.tokens_used == 6


def test_get_llm_provider_requires_selected_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-only")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_llm_provider()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)
    assert provider.provider_id == "openai"
