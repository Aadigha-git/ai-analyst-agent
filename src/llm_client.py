"""Nebius AI Builder API wrapper (OpenAI-compatible chat completions)."""

from __future__ import annotations

import os
from typing import Any


class NebiusClient:
    """Thin HTTP client for Nebius OpenAI-compatible chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("NEBIUS_API_KEY", "")
        self.base_url = (
            base_url or os.getenv("NEBIUS_BASE_URL", "https://api.studio.nebius.ai/v1/")
        ).rstrip("/")

    def chat_completions(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST /chat/completions and return the JSON response body."""
        raise NotImplementedError
