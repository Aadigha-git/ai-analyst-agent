"""Run logger — structured session logs for offline evaluation."""

from __future__ import annotations

from typing import Any


class RunLogger:
    """Records plan, tool calls, results, and verification outcomes."""

    def record(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError
