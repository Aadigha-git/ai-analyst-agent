"""Tests for cross-model / legacy cross-provider comparison reporting (v2-7 / CR-2)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from eval_harness import (  # noqa: E402
    COMPARE_PROVIDERS,
    DEFAULT_NEBIUS_COMPARE_MODELS,
    parse_nebius_compare_models,
    provider_api_key_configured,
    run_compare_models,
    run_compare_providers,
    write_comparison_report,
)


def test_compare_providers_constant_covers_four() -> None:
    assert COMPARE_PROVIDERS == ("nebius", "openai", "anthropic", "google")


def test_default_nebius_compare_models_has_three_tiers() -> None:
    models = parse_nebius_compare_models(DEFAULT_NEBIUS_COMPARE_MODELS)
    assert len(models) == 3
    assert "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" in models
    assert "openai/gpt-oss-120b" in models
    assert "Qwen/Qwen3-235B-A22B-Instruct-2507" in models


def test_parse_nebius_compare_models_from_env(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_COMPARE_MODELS", "a/b, c/d")
    assert parse_nebius_compare_models() == ["a/b", "c/d"]


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
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": [
            {"id": "BQ-01", "passed": True},
            {"id": "BQ-11", "passed": True},
            {"id": "BQ-12", "passed": True},
        ],
        "openai/gpt-oss-120b": [
            {"id": "BQ-01", "passed": True},
            {"id": "BQ-11", "passed": False},
            {"id": "BQ-12", "passed": True},
        ],
    }
    path = tmp_path / "comparison_v2.md"
    write_comparison_report(
        results,
        questions=questions,
        skipped=[("Qwen/Qwen3-235B-A22B-Instruct-2507", "quota exceeded")],
        path=path,
        key_header="Model",
        title="Cross-model evaluation comparison (v2, Nebius-hosted)",
    )
    text = path.read_text(encoding="utf-8")
    assert "## Model scores" in text
    assert "| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | **3/3** |" in text
    assert "| `openai/gpt-oss-120b` | **2/3** |" in text
    assert "| `Qwen/Qwen3-235B-A22B-Instruct-2507` | _skipped_ |" in text
    assert "## Notable differences" in text
    assert "BQ-11" in text
    assert "glossary-default / disclosure" in text
    assert "trap" in text
    assert "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS" in text
    assert "openai/gpt-oss-120b=FAIL" in text
    assert "Not run in CI" in text


def test_write_comparison_report_provider_legacy(tmp_path: Path) -> None:
    questions = [
        {"id": "BQ-01", "question": "x", "tests_glossary": False, "is_trap": False},
    ]
    path = tmp_path / "comparison_v2.md"
    write_comparison_report(
        {"nebius": [{"id": "BQ-01", "passed": True}]},
        questions=questions,
        skipped=[("openai", "`OPENAI_API_KEY` not set")],
        path=path,
        key_header="Provider",
        title="Cross-provider evaluation comparison (v2, legacy)",
    )
    text = path.read_text(encoding="utf-8")
    assert "## Provider scores" in text
    assert "| `nebius` | **1/1** |" in text
    assert "| `openai` | _skipped_ |" in text


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


def test_run_compare_models_requires_nebius_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("run_benchmark must not be called without Nebius key")

    monkeypatch.setattr("eval_harness.run_benchmark", boom)
    rc = run_compare_models(
        [{"id": "BQ-01", "question": "x", "tests_glossary": False, "is_trap": False}],
        models=["openai/gpt-oss-120b"],
        comparison_path=tmp_path / "comparison_v2.md",
    )
    assert rc == 1
    assert called["n"] == 0


def test_provider_api_key_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_api_key_configured("openai") is True
    assert provider_api_key_configured("anthropic") is False
