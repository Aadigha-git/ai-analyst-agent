"""Unit tests for the CI smoke gate (no live LLM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import run_smoke  # noqa: E402


def test_smoke_subset_covers_required_shapes() -> None:
    subset = run_smoke.load_smoke_subset()
    ids = subset["question_ids"]
    assert len(ids) == 5
    questions = run_smoke.resolve_questions(subset)
    assert [q["id"] for q in questions] == ids
    singles = [
        q for q in questions if not q["requires_multi_step"] and not q["is_trap"]
    ]
    multis = [q for q in questions if q["requires_multi_step"] and not q["is_trap"]]
    trap_or_gloss = [
        q for q in questions if q.get("is_trap") or q.get("tests_glossary")
    ]
    assert len(singles) >= 1
    assert len(multis) >= 2
    assert len(trap_or_gloss) >= 2


def test_baseline_gate_fails_when_score_drops(tmp_path: Path, monkeypatch) -> None:
    baseline = {
        "passed": 5,
        "total": 5,
        "score": "5/5",
        "updated": "2026-09-18",
        "question_ids": ["BQ-01", "BQ-08", "BQ-09", "BQ-11", "BQ-12"],
    }
    base_path = tmp_path / "smoke_baseline.json"
    base_path.write_text(json.dumps(baseline))

    fake_rows = [
        {"id": "BQ-01", "passed": True},
        {"id": "BQ-08", "passed": True},
        {"id": "BQ-09", "passed": False},
        {"id": "BQ-11", "passed": False},
        {"id": "BQ-12", "passed": True},
    ]

    monkeypatch.setattr(run_smoke, "BASELINE_PATH", base_path)
    monkeypatch.setattr(
        run_smoke,
        "resolve_questions",
        lambda subset: [{"id": i} for i in baseline["question_ids"]],
    )
    monkeypatch.setattr(
        run_smoke,
        "run_benchmark",
        lambda questions, **kwargs: (fake_rows, 3, 5),
    )

    assert run_smoke.main([]) == 1


def test_baseline_gate_passes_when_score_meets(tmp_path: Path, monkeypatch) -> None:
    baseline = {
        "passed": 4,
        "total": 5,
        "score": "4/5",
        "updated": "2026-09-18",
        "question_ids": ["BQ-01", "BQ-08", "BQ-09", "BQ-11", "BQ-12"],
    }
    base_path = tmp_path / "smoke_baseline.json"
    base_path.write_text(json.dumps(baseline))
    fake_rows = [{"id": i, "passed": True} for i in baseline["question_ids"]]
    fake_rows[-1]["passed"] = False

    monkeypatch.setattr(run_smoke, "BASELINE_PATH", base_path)
    monkeypatch.setattr(
        run_smoke,
        "resolve_questions",
        lambda subset: [{"id": i} for i in baseline["question_ids"]],
    )
    monkeypatch.setattr(
        run_smoke,
        "run_benchmark",
        lambda questions, **kwargs: (fake_rows, 4, 5),
    )

    assert run_smoke.main([]) == 0
