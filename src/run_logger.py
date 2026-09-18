"""Run logger — structured, redacted session traces for offline eval (v2 ADR-010 / BR-15, BR-17)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from llm_client import DEFAULT_MODELS, DEFAULT_PROVIDER

StepType = Literal["plan", "tool_call", "observe", "verify"]

# Approximate blended USD per 1M tokens (input/output averaged). Estimates only —
# not billing-grade; used for rough cost_estimate_usd in traces.
_PRICE_PER_MTOK: dict[tuple[str, str], float] = {
    ("nebius", "meta-llama/Llama-3.3-70B-Instruct"): 0.25,
    ("openai", "gpt-4o-mini"): 0.30,
    ("openai", "gpt-4o"): 5.00,
    ("anthropic", "claude-sonnet-4-20250514"): 6.00,
    ("google", "gemini-2.0-flash"): 0.20,
}

_PROVIDER_DEFAULT_PER_MTOK: dict[str, float] = {
    "nebius": 0.25,
    "openai": 0.50,
    "anthropic": 5.00,
    "google": 0.25,
}

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def resolve_provider_model(
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Resolve provider/model from args or ``LLM_PROVIDER`` / ``LLM_MODEL``."""
    pname = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    mname = (
        model
        or os.getenv("LLM_MODEL")
        or DEFAULT_MODELS.get(pname)
        or DEFAULT_MODELS[DEFAULT_PROVIDER]
    )
    return pname, mname


def estimate_cost_usd(
    tokens_used: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> float:
    """Return an approximate USD cost for ``tokens_used`` (estimate only)."""
    pname, mname = resolve_provider_model(provider, model)
    rate = _PRICE_PER_MTOK.get((pname, mname))
    if rate is None:
        rate = _PROVIDER_DEFAULT_PER_MTOK.get(pname, 1.0)
    tokens = max(0, int(tokens_used or 0))
    return round((tokens / 1_000_000.0) * rate, 8)


def _columns_from_rows(rows: list[Any]) -> list[str]:
    if not rows:
        return []
    first = rows[0]
    if isinstance(first, dict):
        return [str(k) for k in first.keys()]
    return []


def redact_payload(payload: Any) -> Any:
    """Replace tabular row values with type/shape placeholders (default safe mode)."""
    if isinstance(payload, dict):
        if "rows" in payload and isinstance(payload["rows"], list):
            rows = payload["rows"]
            columns = payload.get("columns")
            if not isinstance(columns, list) or not columns:
                columns = _columns_from_rows(rows)
            row_count = payload.get("row_count")
            if not isinstance(row_count, int):
                row_count = len(rows)
            out = {
                k: redact_payload(v) for k, v in payload.items() if k not in {"rows"}
            }
            out["rows"] = {
                "_redacted": True,
                "row_count": row_count,
                "columns": list(columns),
            }
            return out
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


class RunLogger:
    """Records one JSON object per investigation step to ``logs/run_<timestamp>.jsonl``."""

    def __init__(
        self,
        *,
        redact: bool = True,
        log_dir: Path | str | None = None,
        path: Path | str | None = None,
        provider: str | None = None,
        model: str | None = None,
        session_started_at: datetime | None = None,
    ) -> None:
        self.redact = bool(redact)
        self.provider, self.model = resolve_provider_model(provider, model)
        started = session_started_at or datetime.now(timezone.utc)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        directory = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
        directory.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path is not None else directory / f"run_{stamp}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure the file exists even before the first record.
        if not self.path.exists():
            self.path.touch()
        self._closed = False

    def record(
        self,
        step_type: StepType,
        *,
        payload: Any = None,
        latency_ms: float | int = 0,
        tokens_used: int = 0,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one step event; return the written object."""
        if self._closed:
            raise RuntimeError("RunLogger is closed")
        if step_type not in {"plan", "tool_call", "observe", "verify"}:
            raise ValueError(f"invalid step_type: {step_type!r}")

        ts = timestamp or datetime.now(timezone.utc)
        safe_payload = redact_payload(payload) if self.redact else _json_safe(payload)
        event = {
            "step_type": step_type,
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "latency_ms": int(round(float(latency_ms or 0))),
            "tokens_used": int(tokens_used or 0),
            "cost_estimate_usd": estimate_cost_usd(
                int(tokens_used or 0),
                provider=self.provider,
                model=self.model,
            ),
            "payload": safe_payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        return event

    def read_events(self) -> list[dict[str, Any]]:
        """Load all events written so far (for tests / replay)."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
        return events

    def close(self) -> None:
        self._closed = True


def _json_safe(value: Any) -> Any:
    """Round-trip through JSON so traces stay serializable when redaction is off."""
    return json.loads(json.dumps(value, default=str))
