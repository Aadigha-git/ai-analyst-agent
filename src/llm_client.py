"""Nebius AI Builder API wrapper (OpenAI-compatible chat completions)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.studio.nebius.ai/v1"
DEFAULT_MODEL = "zai-org/GLM-5.2"


@dataclass
class ToolCall:
    """A single function/tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LlmResult:
    """Normalized chat result: either a tool call or final text."""

    kind: Literal["tool_call", "text"]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class NebiusClient:
    """Thin HTTP client for Nebius OpenAI-compatible chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("NEBIUS_API_KEY", "")
        )
        self.base_url = (
            base_url or os.getenv("NEBIUS_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = model or os.getenv("NEBIUS_MODEL", DEFAULT_MODEL)
        self._session = session or requests.Session()

    def chat_completions(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST /chat/completions and return the JSON response body."""
        if not self.api_key:
            raise RuntimeError("NEBIUS_API_KEY is not set.")

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            **kwargs,
        }
        if tools is not None:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        response = self._session.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=float(os.getenv("NEBIUS_TIMEOUT_SECONDS", "60")),
        )
        response.raise_for_status()
        body = response.json()
        self._log_usage(body.get("usage") or {})
        return body

    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LlmResult:
        """Run a chat completion and return a tool call or final text response.

        ``tools`` must use the OpenAI tools format:
        ``{"type": "function", "function": {"name", "description", "parameters"}}``.
        """
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        body = self.chat_completions(
            messages=full_messages,
            model=model,
            tools=tools,
            **kwargs,
        )
        return self._parse_result(body)

    def _log_usage(self, usage: dict[str, Any]) -> None:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        )
        logger.info(
            "nebius_token_usage prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )

    def _parse_result(self, body: dict[str, Any]) -> LlmResult:
        usage_raw = body.get("usage") or {}
        usage = {
            "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
            "total_tokens": int(
                usage_raw.get("total_tokens")
                or (
                    int(usage_raw.get("prompt_tokens") or 0)
                    + int(usage_raw.get("completion_tokens") or 0)
                )
            ),
        }
        choices = body.get("choices") or []
        if not choices:
            return LlmResult(kind="text", text="", usage=usage, raw=body)

        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            first = tool_calls[0]
            function = first.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    arguments = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid tool call arguments JSON: {raw_args}"
                    ) from exc
            elif isinstance(raw_args, dict):
                arguments = raw_args
            else:
                raise ValueError(
                    f"Unexpected tool call arguments type: {type(raw_args)}"
                )

            return LlmResult(
                kind="tool_call",
                tool_call=ToolCall(
                    id=str(first.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                ),
                usage=usage,
                raw=body,
            )

        return LlmResult(
            kind="text",
            text=message.get("content") or "",
            usage=usage,
            raw=body,
        )


# OpenAI-compatible tool schema for run_sql (Document 5 contract).
RUN_SQL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": (
            "Execute a single read-only SELECT against the target PostgreSQL database. "
            "Returns capped rows, row_count, execution_time_seconds, truncated, and columns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single SELECT (or WITH … SELECT) statement.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
