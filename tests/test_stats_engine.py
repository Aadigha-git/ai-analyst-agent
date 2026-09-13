"""Tests for the sandboxed allow-listed stats engine (ADR-004)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools.stats_engine import (  # noqa: E402
    StatsOperationNotAllowed,
    run_stats,
)


@pytest.fixture
def sales_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region": ["North", "North", "South", "South", "East"],
            "month": [1, 2, 1, 2, 1],
            "revenue": [10.0, 20.0, 30.0, 40.0, 100.0],
            "units": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )


def test_aggregate(sales_df: pd.DataFrame) -> None:
    out = run_stats(
        "aggregate",
        {"group_by": "region", "column": "revenue", "func": "sum"},
        "sales",
        data_store={"sales": sales_df},
    )
    rows = {r["region"]: r["revenue"] for r in out["result"]}
    assert rows["North"] == 30.0
    assert rows["South"] == 70.0
    assert rows["East"] == 100.0


def test_rolling_mean(sales_df: pd.DataFrame) -> None:
    out = run_stats(
        "rolling_mean",
        {"column": "revenue", "window": 2},
        "sales",
        data_store={"sales": sales_df},
    )
    values = [r["revenue_rolling_mean_2"] for r in out["result"]]
    assert pd.isna(values[0])
    assert values[1] == pytest.approx(15.0)
    assert values[2] == pytest.approx(25.0)


def test_outlier_zscore(sales_df: pd.DataFrame) -> None:
    out = run_stats(
        "outlier_zscore",
        {"column": "revenue", "threshold": 1.5},
        "sales",
        data_store={"sales": sales_df},
    )
    assert out["result"]["outlier_count"] >= 1
    assert any(r["is_outlier"] for r in out["result"]["rows"])


def test_correlation(sales_df: pd.DataFrame) -> None:
    out = run_stats(
        "correlation",
        {"col_a": "revenue", "col_b": "units"},
        "sales",
        data_store={"sales": sales_df},
    )
    assert out["result"]["correlation"] == pytest.approx(
        sales_df["revenue"].corr(sales_df["units"])
    )


def test_segment(sales_df: pd.DataFrame) -> None:
    out = run_stats(
        "segment",
        {"group_col": "region", "metric_col": "revenue", "func": "mean"},
        "sales",
        data_store={"sales": sales_df},
    )
    rows = {r["region"]: r["revenue_mean"] for r in out["result"]}
    assert rows["North"] == pytest.approx(15.0)
    assert rows["South"] == pytest.approx(35.0)
    assert rows["East"] == pytest.approx(100.0)


def test_unlisted_operation_rejected(sales_df: pd.DataFrame) -> None:
    with pytest.raises(StatsOperationNotAllowed, match="not allow-listed"):
        run_stats(
            "exec_malicious",
            {"code": "import os; os.system('id')"},
            "sales",
            data_store={"sales": sales_df},
        )
