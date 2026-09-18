"""Load a config-driven semantic glossary for orchestrator planning (v2 ADR-007 / BR-11)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOSSARY_PATH = _REPO_ROOT / "config" / "glossary.yaml"

# Heuristic: question already specifies a time window (no glossary default needed).
_TIME_RANGE_HINT = re.compile(
    r"\b("
    r"20\d{2}|q[1-4]|ytd|mtd|trailing|"
    r"last\s+\d+|past\s+\d+|previous\s+\d+|"
    r"between\s+|since\s+|until\s+|from\s+20|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"\d+\s*(day|week|month|year)s?"
    r")\b",
    re.IGNORECASE,
)

_METRIC_HINT = re.compile(
    r"\b(revenue|total|sum|count|average|avg|aov|sales|orders?|customers?|products?)\b",
    re.IGNORECASE,
)


def load_glossary(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Load glossary YAML into a term → fields mapping.

    Missing or unreadable files yield an empty dict (with a warning) — never raise
    for the default missing-file case. Malformed YAML also yields empty + warning.
    """
    glossary_path = Path(path) if path is not None else DEFAULT_GLOSSARY_PATH
    if not glossary_path.is_file():
        logger.warning(
            "Glossary file not found at %s; continuing with empty glossary",
            glossary_path,
        )
        return {}

    try:
        import yaml
    except ImportError:  # pragma: no cover — dependency listed in requirements.txt
        logger.warning("PyYAML is not installed; continuing with empty glossary")
        return {}

    try:
        raw = yaml.safe_load(glossary_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — never crash the agent on bad glossary
        logger.warning(
            "Failed to read glossary at %s: %s; continuing empty", glossary_path, exc
        )
        return {}

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "Glossary at %s must be a mapping of terms; got %s; continuing empty",
            glossary_path,
            type(raw).__name__,
        )
        return {}

    parsed: dict[str, dict[str, Any]] = {}
    for term, value in raw.items():
        if not isinstance(term, str) or not term.strip():
            continue
        if not isinstance(value, dict):
            logger.warning("Glossary term %r is not a mapping; skipping", term)
            continue
        entry = {str(k): v for k, v in value.items() if v is not None}
        if "definition" not in entry or not str(entry["definition"]).strip():
            logger.warning("Glossary term %r missing definition; skipping", term)
            continue
        parsed[term.strip()] = entry
    return parsed


def format_glossary_context(
    glossary: dict[str, dict[str, Any]] | None = None,
    *,
    path: Path | str | None = None,
) -> str:
    """Return a plain-text block for the planning prompt, or '' if empty."""
    entries = glossary if glossary is not None else load_glossary(path)
    if not entries:
        return ""

    lines = [
        "Semantic glossary (canonical business terms — prefer these over guessing):",
    ]
    for term in sorted(entries):
        fields = entries[term]
        definition = str(fields.get("definition", "")).strip()
        lines.append(f"- {term}: {definition}")
        join = fields.get("default_join")
        if join:
            lines.append(f"  default_join: {join}")
        for key, value in fields.items():
            if key in {"definition", "default_join"}:
                continue
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def detect_defaults_used(
    question: str,
    glossary: dict[str, dict[str, Any]] | None = None,
    *,
    path: Path | str | None = None,
) -> list[str]:
    """Return human-readable glossary defaults that apply to ``question``.

    Used to populate ``InvestigationState.defaults_used`` when planning leans on
    glossary fallbacks (e.g. all-dates window for open-ended revenue totals).
    """
    entries = glossary if glossary is not None else load_glossary(path)
    if not entries or not (question or "").strip():
        return []

    q = question.strip()
    used: list[str] = []

    if "default_time_window" in entries and _METRIC_HINT.search(q):
        if not _TIME_RANGE_HINT.search(q):
            used.append("using all available dates, since no time range was specified")

    if "revenue" in entries and re.search(r"\brevenue\b", q, re.IGNORECASE):
        used.append("revenue means sum of order_items.line_total")

    if "average_order_value" in entries and re.search(
        r"\b(aov|average order value)\b", q, re.IGNORECASE
    ):
        used.append(
            "average order value is the mean of per-order SUM(line_total), "
            "not AVG(line_total)"
        )

    if "active_customer" in entries and re.search(
        r"\bactive customers?\b", q, re.IGNORECASE
    ):
        used.append(
            "active customer means a customer with at least one order in the "
            "trailing 90 days"
        )

    seen: set[str] = set()
    out: list[str] = []
    for item in used:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
