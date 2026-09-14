"""Model-agnostic LLM provider layer (CR-1a / ADR-011).

Internal tool schemas use the OpenAI tools shape documented in ``docs/API.md``.
Each concrete provider translates that schema into its native function-calling
format and normalizes responses into ``LLMResponse``.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ToolSchema = dict[str, Any]

DEFAULT_PROVIDER = "nebius"

DEFAULT_MODELS: dict[str, str] = {
    "nebius": "meta-llama/Llama-3.3-70B-Instruct",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-2.0-flash",
}

NEBIUS_DEFAULT_BASE_URL = "https://api.studio.nebius.ai/v1"

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "nebius": "NEBIUS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}


@dataclass
class LLMResponse:
    """Normalized chat result across all providers."""

    type: Literal["tool_call", "text"]
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    text: str | None = None
    tokens_used: int = 0


# --- Backward-compatible aliases used by older tests / spike code -------------


@dataclass
class ToolCall:
    """Legacy tool-call shape; prefer ``LLMResponse.tool_name`` / ``tool_args``."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LlmResult:
    """Legacy result shape mapping onto ``LLMResponse``."""

    kind: Literal["tool_call", "text"]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_llm_response(cls, response: LLMResponse) -> LlmResult:
        usage = {"total_tokens": response.tokens_used}
        if response.type == "tool_call":
            return cls(
                kind="tool_call",
                tool_call=ToolCall(
                    id="",
                    name=response.tool_name or "",
                    arguments=dict(response.tool_args or {}),
                ),
                usage=usage,
            )
        return cls(kind="text", text=response.text or "", usage=usage)


def _parse_json_args(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        if not raw_args.strip():
            return {}
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tool call arguments JSON: {raw_args}") from exc
        if not isinstance(parsed, dict):
            raise TypeError(
                f"Tool call arguments must be an object, got {type(parsed)}"
            )
        return parsed
    raise TypeError(f"Unexpected tool call arguments type: {type(raw_args)}")


def _openai_style_tools(tools: list[ToolSchema] | None) -> list[ToolSchema] | None:
    return tools


def _to_anthropic_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or tool
        converted.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return converted


def _to_google_declarations(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or tool
        declarations.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "parameters": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return declarations


def _tokens_from_openai_usage(usage: Any) -> int:
    if not usage:
        return 0
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is not None:
            return int(total)
        return int(usage.get("prompt_tokens") or 0) + int(
            usage.get("completion_tokens") or 0
        )
    total = getattr(usage, "total_tokens", None)
    if total is not None:
        return int(total)
    return int(getattr(usage, "prompt_tokens", 0) or 0) + int(
        getattr(usage, "completion_tokens", 0) or 0
    )


class LLMProvider(ABC):
    """Abstract LLM backend used by the orchestrator, verifier, and formatter."""

    provider_id: str
    model: str

    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Run one chat turn; ``tools`` use the internal OpenAI tools schema."""

    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **_kwargs: Any,
    ) -> LLMResponse:
        """Alias for ``chat`` (call sites historically used ``complete``)."""
        return self.chat(system_prompt=system_prompt, messages=messages, tools=tools)


class NebiusProvider(LLMProvider):
    """OpenAI-compatible Nebius AI Builder client (existing v1 behavior)."""

    provider_id = "nebius"

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
            base_url or os.getenv("NEBIUS_BASE_URL", NEBIUS_DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = (
            model
            or os.getenv("LLM_MODEL")
            or os.getenv("NEBIUS_MODEL", DEFAULT_MODELS["nebius"])
        )
        self._session = session or requests.Session()

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("NEBIUS_API_KEY is not set.")

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
        }
        openai_tools = _openai_style_tools(tools)
        if openai_tools is not None:
            payload["tools"] = openai_tools

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
        tokens = _tokens_from_openai_usage(body.get("usage") or {})
        logger.info(
            "nebius_token_usage prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            int((body.get("usage") or {}).get("prompt_tokens") or 0),
            int((body.get("usage") or {}).get("completion_tokens") or 0),
            tokens,
        )
        return self._parse_openai_compatible(body, tokens)

    @staticmethod
    def _parse_openai_compatible(body: dict[str, Any], tokens: int) -> LLMResponse:
        choices = body.get("choices") or []
        if not choices:
            return LLMResponse(type="text", text="", tokens_used=tokens)

        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            first = tool_calls[0]
            function = first.get("function") or {}
            return LLMResponse(
                type="tool_call",
                tool_name=str(function.get("name") or ""),
                tool_args=_parse_json_args(function.get("arguments")),
                tokens_used=tokens,
            )
        return LLMResponse(
            type="text",
            text=message.get("content") or "",
            tokens_used=tokens,
        )


# Backward-compatible name used by older imports / monkeypatches.
NebiusClient = NebiusProvider


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions via the official ``openai`` SDK."""

    provider_id = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        )
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODELS["openai"])
        self._client = client

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key)
        return self._client

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        client = self._sdk()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
        }
        if tools is not None:
            kwargs["tools"] = _openai_style_tools(tools)

        completion = client.chat.completions.create(**kwargs)
        tokens = _tokens_from_openai_usage(getattr(completion, "usage", None))
        choice = completion.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None) or []
        if tool_calls:
            first = tool_calls[0]
            function = first.function
            return LLMResponse(
                type="tool_call",
                tool_name=function.name,
                tool_args=_parse_json_args(function.arguments),
                tokens_used=tokens,
            )
        return LLMResponse(
            type="text",
            text=choice.content or "",
            tokens_used=tokens,
        )


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API via the official ``anthropic`` SDK."""

    provider_id = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")
        )
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODELS["anthropic"])
        self._client = client

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        from anthropic import Anthropic

        self._client = Anthropic(api_key=self.api_key)
        return self._client

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        client = self._sdk()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": 4096,
        }
        if tools is not None:
            kwargs["tools"] = _to_anthropic_tools(tools)

        response = client.messages.create(**kwargs)
        usage = getattr(response, "usage", None)
        tokens = 0
        if usage is not None:
            tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
                getattr(usage, "output_tokens", 0) or 0
            )

        for block in response.content or []:
            if getattr(block, "type", None) == "tool_use":
                return LLMResponse(
                    type="tool_call",
                    tool_name=getattr(block, "name", "") or "",
                    tool_args=dict(getattr(block, "input", None) or {}),
                    tokens_used=tokens,
                )

        text_parts = [
            getattr(block, "text", "")
            for block in (response.content or [])
            if getattr(block, "type", None) == "text"
        ]
        return LLMResponse(
            type="text",
            text="".join(text_parts),
            tokens_used=tokens,
        )


class GoogleProvider(LLMProvider):
    """Google Gemini via the ``google-genai`` SDK."""

    provider_id = "google"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("GOOGLE_API_KEY", "")
        )
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODELS["google"])
        self._client = client

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set.")
        from google import genai

        self._client = genai.Client(api_key=self.api_key)
        return self._client

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        client = self._sdk()
        # Flatten OpenAI-style messages into a single user prompt for simplicity.
        user_chunks: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            user_chunks.append(f"{role}: {content}")
        prompt = "\n\n".join(user_chunks) if user_chunks else ""

        config: dict[str, Any] = {"system_instruction": system_prompt}
        if tools is not None:
            config["tools"] = [
                {"function_declarations": _to_google_declarations(tools)}
            ]

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        tokens = 0
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            tokens = int(getattr(usage, "total_token_count", 0) or 0)

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                fn_call = getattr(part, "function_call", None)
                if fn_call is not None:
                    args = getattr(fn_call, "args", None) or {}
                    if hasattr(args, "items"):
                        args = dict(args)
                    return LLMResponse(
                        type="tool_call",
                        tool_name=getattr(fn_call, "name", "") or "",
                        tool_args=dict(args),
                        tokens_used=tokens,
                    )

        text = getattr(response, "text", None)
        if text is None and candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            text = "".join(getattr(p, "text", "") or "" for p in parts)
        return LLMResponse(type="text", text=text or "", tokens_used=tokens)


def get_llm_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
) -> LLMProvider:
    """Instantiate the provider selected by ``LLM_PROVIDER`` (default: nebius).

    Only the selected provider's API key is required. Raises a clear startup
    error naming the missing variable otherwise.
    """
    name = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    key_env = PROVIDER_API_KEY_ENV.get(name)
    if key_env is None:
        supported = ", ".join(sorted(PROVIDER_API_KEY_ENV))
        raise RuntimeError(
            f"Unknown LLM_PROVIDER={name!r}. Supported values: {supported}."
        )

    api_key = os.getenv(key_env, "")
    if not api_key:
        raise RuntimeError(
            f"LLM_PROVIDER={name!r} requires {key_env} to be set in the environment "
            f"(only the selected provider's key is required)."
        )

    resolved_model = model or os.getenv("LLM_MODEL") or DEFAULT_MODELS[name]

    if name == "nebius":
        return NebiusProvider(api_key=api_key, model=resolved_model)
    if name == "openai":
        return OpenAIProvider(api_key=api_key, model=resolved_model)
    if name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=resolved_model)
    if name == "google":
        return GoogleProvider(api_key=api_key, model=resolved_model)

    raise RuntimeError(f"Unhandled LLM_PROVIDER={name!r}.")


# OpenAI-compatible tool schema for run_sql (Document 5 contract).
RUN_SQL_TOOL_SCHEMA: ToolSchema = {
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
