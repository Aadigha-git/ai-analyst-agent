"""Tests for cross-provider comparison reporting (v2-7 / BR-14)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from eval_harness import (  # noqa: E402
    COMPARE_PROVIDERS,
    provider_api_key_configured,
    run_compare_providers,
    write_comparison_report,
)


def test_compare_providers_constant_covers_four() -> None:
    assert COMPARE_PROVIDERS == ("nebius", "openai", "anthropic", "google")


def test_write_comparison_report_scores_and_disagreements(tmp_path: Path) -> None:
    questions = [
        {
            "id": "BQ-01",
            "question": "How many orders?",
            "tests_glossary": False,
            "is_trap": False,
        },
        {
            "id": "BQ-11",
            "question": "What is total Electronics revenue?",
            "tests_glossary": True,
            "is_trap": True,
        },
        {
            "id": "BQ-12",
            "question": "How are sales performing recently?",
            "tests_glossary": True,
            "is_trap": True,
        },
    ]
    results = {
        "nebius": [
            {"id": "BQ-01", "passed": True},
            {"id": "BQ-11", "passed": True},
            {"id": "BQ-12", "passed": True},
        ],
        "openai": [
            {"id": "BQ-01", "passed": True},
            {"id": "BQ-11", "passed": False},
            {"id": "BQ-12", "passed": True},
        ],
    }
    path = tmp_path / "comparison_v2.md"
    write_comparison_report(
        results,
        questions=questions,
        skipped=[("anthropic", "`ANTHROPIC_API_KEY` not set")],
        path=path,
    )
    text = path.read_text(encoding="utf-8")
    assert "| nebius | **3/3** |" in text
    assert "| openai | **2/3** |" in text
    assert "| anthropic | _skipped_ |" in text
    assert "## Notable differences" in text
    assert "BQ-11" in text
    assert "glossary-default / disclosure" in text
    assert "trap" in text
    assert "nebius=PASS" in text and "openai=FAIL" in text
    assert "Not run in CI" in text


def test_run_compare_providers_skips_missing_keys(tmp_path: Path, monkeypatch) -> None:
    """Missing keys warn+skip; do not raise. No live LLM calls."""
    for env in (
        "NEBIUS_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)

    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("run_benchmark must not be called without keys")

    monkeypatch.setattr("eval_harness.run_benchmark", boom)
    out = tmp_path / "comparison_v2.md"
    rc = run_compare_providers(
        [{"id": "BQ-01", "question": "x", "tests_glossary": False, "is_trap": False}],
        comparison_path=out,
    )
    assert rc == 1  # no provider produced scores
    assert called["n"] == 0
    assert out.exists()
    assert "skipped" in out.read_text(encoding="utf-8").lower()


def test_provider_api_key_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_api_key_configured("openai") is True
    assert provider_api_key_configured("anthropic") is False
