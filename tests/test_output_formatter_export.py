"""Tests for chart recommendation + CSV/XLSX export (v2-8 / BR-19)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cli import app  # noqa: E402
from output_formatter import (  # noqa: E402
    export_dataframe,
    recommend_chart,
)


def test_recommend_chart_line_for_datetime() -> None:
    df = pd.DataFrame(
        {
            "order_date": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
            "revenue": [100.0, 120.0, 90.0],
        }
    )
    result = recommend_chart(df)
    assert result["chart_type"] == "line"
    assert "Suggested visualization: line chart" in result["suggestion"]
    assert "order_date" in result["suggestion"]
    assert "revenue" in result["suggestion"]


def test_recommend_chart_bar_for_category_and_numeric() -> None:
    df = pd.DataFrame(
        {
            "region": ["East", "West", "Central"],
            "total revenue": [50, 34, 37],
        }
    )
    result = recommend_chart(df)
    assert result["chart_type"] == "bar"
    assert result["suggestion"] == (
        "Suggested visualization: bar chart (region vs. total revenue)"
    )


def test_recommend_chart_scatter_for_two_numerics() -> None:
    df = pd.DataFrame({"quantity": [1, 2, 3], "line_total": [10.5, 20.0, 15.25]})
    result = recommend_chart(df)
    assert result["chart_type"] == "scatter"
    assert "scatter chart (quantity vs. line_total)" in result["suggestion"]


def test_recommend_chart_table_only_otherwise() -> None:
    df = pd.DataFrame({"label": ["a", "b"], "note": ["x", "y"]})
    result = recommend_chart(df)
    assert result["chart_type"] == "table only"
    assert result["suggestion"] == "Suggested visualization: table only"


def test_export_csv_and_xlsx_are_readable(tmp_path: Path) -> None:
    df = pd.DataFrame({"region": ["East", "West"], "orders": [50, 34]})
    stamp = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)

    csv_path = export_dataframe(df, "csv", output_dir=tmp_path, timestamp=stamp)
    assert csv_path.name == "export_20260918T120000Z.csv"
    assert csv_path.is_file()
    loaded_csv = pd.read_csv(csv_path)
    assert list(loaded_csv.columns) == ["region", "orders"]
    assert loaded_csv["orders"].tolist() == [50, 34]

    xlsx_path = export_dataframe(df, "xlsx", output_dir=tmp_path, timestamp=stamp)
    assert xlsx_path.name == "export_20260918T120000Z.xlsx"
    assert xlsx_path.is_file()
    loaded_xlsx = pd.read_excel(xlsx_path, engine="openpyxl")
    assert list(loaded_xlsx.columns) == ["region", "orders"]
    assert loaded_xlsx["orders"].tolist() == [50, 34]


def test_cli_export_flag_writes_file(tmp_path: Path, monkeypatch) -> None:
    class _FakeLLM:
        provider_id = "nebius"
        model = "meta-llama/Llama-3.3-70B-Instruct"

    rows = [{"region": "East", "orders": 50}]
    evidence_rows = [
        {
            "action": "run_sql",
            "result": {
                "columns": ["region", "orders"],
                "rows": rows,
                "row_count": 1,
            },
        }
    ]

    class _State:
        def __init__(self) -> None:
            self.evidence = evidence_rows
            self.defaults_used: list[str] = []

    def fake_investigate(*args, **kwargs):
        return {
            "status": "ok",
            "answer": "East has 50 orders.",
            "state": _State(),
            "trace": [],
        }

    def fake_format_output(answer, evidence, **kwargs):
        return {
            "narrative": answer,
            "table": {
                "columns": ["region", "orders"],
                "rows": rows,
            },
            "defaults_used": [],
            "chart_recommendation": {
                "chart_type": "bar",
                "suggestion": "Suggested visualization: bar chart (region vs. orders)",
            },
        }

    monkeypatch.setattr("cli.get_llm_provider", lambda: _FakeLLM())
    monkeypatch.setattr("cli.print_cli_banner", lambda *a, **k: None)
    monkeypatch.setattr("cli.investigate", fake_investigate)
    monkeypatch.setattr("cli.format_output", fake_format_output)
    monkeypatch.setattr(
        "cli.RunLogger",
        lambda **kwargs: MagicMock(path=tmp_path / "run.jsonl", close=lambda: None),
    )
    monkeypatch.setattr(
        "cli.export_dataframe",
        lambda df, fmt, output_dir=None, timestamp=None: export_dataframe(
            df, fmt, output_dir=tmp_path, timestamp=timestamp
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["ask", "Orders by region?", "--export", "csv"])
    assert result.exit_code == 0, result.output
    assert "Suggested visualization: bar chart" in result.output
    exports = list(tmp_path.glob("export_*.csv"))
    assert len(exports) == 1
    loaded = pd.read_csv(exports[0])
    assert loaded["region"].tolist() == ["East"]
