"""Tests for the semantic glossary loader (v2-1 / BR-11)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from glossary_loader import (  # noqa: E402
    DEFAULT_GLOSSARY_PATH,
    detect_defaults_used,
    format_glossary_context,
    load_glossary,
)

SEEDED_TERMS = {
    "active_customer",
    "revenue",
    "average_order_value",
    "default_time_window",
    "sales",
}


def test_load_seeded_glossary_parses_each_entry() -> None:
    glossary = load_glossary(DEFAULT_GLOSSARY_PATH)
    assert set(glossary) == SEEDED_TERMS
    for term, entry in glossary.items():
        assert isinstance(entry, dict)
        assert "definition" in entry
        assert str(entry["definition"]).strip()
    assert "default_join" in glossary["active_customer"]
    assert "line_total" in glossary["revenue"]["definition"]
    assert "all available data" in glossary["default_time_window"]["definition"]


def test_format_glossary_context_includes_terms() -> None:
    block = format_glossary_context(load_glossary())
    assert "Semantic glossary" in block
    assert "revenue:" in block
    assert "default_time_window:" in block
    assert "default_join:" in block


def test_detect_defaults_used_for_open_ended_revenue() -> None:
    used = detect_defaults_used("What is total Electronics revenue?")
    assert any("all available dates" in u for u in used)
    assert any("revenue" in u.lower() for u in used)


def test_detect_defaults_skips_time_window_when_year_specified() -> None:
    used = detect_defaults_used("What is total Electronics revenue in 2024?")
    assert not any("all available dates" in u for u in used)


def test_missing_glossary_file_returns_empty_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with caplog.at_level(logging.WARNING, logger="glossary_loader"):
        glossary = load_glossary(missing)
    assert glossary == {}
    assert format_glossary_context(glossary) == ""
    assert "not found" in caplog.text.lower()
