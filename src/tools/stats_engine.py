"""Stats engine — allow-listed pandas/statistical operations on in-memory data.

ADR-004: fixed operation map only — never exec/eval of agent-supplied code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd

ALLOW_LISTED_OPERATIONS = (
    "aggregate",
    "rolling_mean",
    "outlier_zscore",
    "correlation",
    "segment",
)


class StatsOperationNotAllowed(ValueError):
    """Raised when ``operation`` is not in the allow-list (never exec/eval fallback)."""


class StatsDataRefError(ValueError):
    """Raised when ``data_ref`` cannot be resolved to in-memory rows."""


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, Mapping) and "rows" in data:
        return pd.DataFrame(list(data["rows"]))
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return pd.DataFrame(list(data))
    raise StatsDataRefError(
        f"Unsupported in-memory data type for stats: {type(data).__name__}"
    )


def resolve_data_ref(
    data_ref: str,
    *,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    data_store: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Resolve ``data_ref`` to a DataFrame from evidence or an explicit store.

    Resolution order:
    1. ``data_store[data_ref]`` if provided
    2. ``evidence[int(data_ref)]`` when ``data_ref`` is an integer index
    3. ``"latest"`` → last ``run_sql`` result in ``evidence``
    """
    if data_store is not None and data_ref in data_store:
        return _as_dataframe(data_store[data_ref])

    if evidence is not None:
        if data_ref == "latest":
            for item in reversed(list(evidence)):
                if item.get("action") == "run_sql" and "result" in item:
                    return _as_dataframe(item["result"])
            raise StatsDataRefError(
                "No prior run_sql result found for data_ref='latest'."
            )

        if data_ref.isdigit():
            idx = int(data_ref)
            if idx < 0 or idx >= len(evidence):
                raise StatsDataRefError(
                    f"data_ref index {idx} out of range for evidence length {len(evidence)}."
                )
            item = evidence[idx]
            payload = item.get("result", item)
            return _as_dataframe(payload)

    raise StatsDataRefError(
        f"Cannot resolve data_ref={data_ref!r}; pass data_store or evidence from the orchestrator."
    )


def _aggregate(df: pd.DataFrame, params: dict[str, Any]) -> Any:
    group_by = params.get("group_by") or params.get("by")
    if not group_by:
        raise ValueError("aggregate requires params.group_by")
    if isinstance(group_by, str):
        group_by = [group_by]

    agg = params.get("agg")
    if agg is None:
        column = params.get("column") or params.get("metric_col")
        func = params.get("func") or params.get("agg_func") or "sum"
        if not column:
            raise ValueError(
                "aggregate requires params.agg or params.column + params.func"
            )
        agg = {column: func}

    out = df.groupby(list(group_by), dropna=False).agg(agg).reset_index()
    return out.to_dict(orient="records")


def _rolling_mean(df: pd.DataFrame, params: dict[str, Any]) -> Any:
    column = params.get("column")
    window = params.get("window")
    if not column or window is None:
        raise ValueError("rolling_mean requires params.column and params.window")
    window_i = int(window)
    series = df[column].astype(float).rolling(window=window_i).mean()
    out = df.copy()
    out[f"{column}_rolling_mean_{window_i}"] = series
    return out.to_dict(orient="records")


def _outlier_zscore(df: pd.DataFrame, params: dict[str, Any]) -> Any:
    column = params.get("column")
    if not column:
        raise ValueError("outlier_zscore requires params.column")
    threshold = float(params.get("threshold", 3.0))
    values = df[column].astype(float)
    mean = values.mean()
    std = values.std(ddof=0)
    if std == 0 or pd.isna(std):
        z = pd.Series([0.0] * len(values), index=values.index)
    else:
        z = (values - mean) / std
    out = df.copy()
    out["zscore"] = z
    out["is_outlier"] = z.abs() >= threshold
    outliers = out.loc[out["is_outlier"]].to_dict(orient="records")
    return {
        "threshold": threshold,
        "mean": float(mean) if pd.notna(mean) else None,
        "std": float(std) if pd.notna(std) else None,
        "outlier_count": int(out["is_outlier"].sum()),
        "outliers": outliers,
        "rows": out.to_dict(orient="records"),
    }


def _correlation(df: pd.DataFrame, params: dict[str, Any]) -> Any:
    col_a = params.get("col_a")
    col_b = params.get("col_b")
    if not col_a or not col_b:
        raise ValueError("correlation requires params.col_a and params.col_b")
    corr = df[col_a].astype(float).corr(df[col_b].astype(float))
    return {
        "col_a": col_a,
        "col_b": col_b,
        "correlation": float(corr) if pd.notna(corr) else None,
    }


def _segment(df: pd.DataFrame, params: dict[str, Any]) -> Any:
    group_col = params.get("group_col")
    metric_col = params.get("metric_col")
    if not group_col or not metric_col:
        raise ValueError("segment requires params.group_col and params.metric_col")
    func = params.get("func") or "mean"
    grouped = df.groupby(group_col, dropna=False)[metric_col].agg(func).reset_index()
    grouped = grouped.rename(columns={metric_col: f"{metric_col}_{func}"})
    return grouped.to_dict(orient="records")


# Fixed dict mapping operation names → pandas callables (ADR-004). Never exec/eval.
OPERATIONS: dict[str, Callable[[pd.DataFrame, dict[str, Any]], Any]] = {
    "aggregate": _aggregate,
    "rolling_mean": _rolling_mean,
    "outlier_zscore": _outlier_zscore,
    "correlation": _correlation,
    "segment": _segment,
}


def run_stats(
    operation: str,
    params: dict[str, Any] | None,
    data_ref: str,
    *,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    data_store: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an allow-listed stats operation against previously fetched in-memory data.

    Guardrails: operation must be in ``OPERATIONS``; never opens a DB connection;
    never uses exec/eval. ``data_ref`` resolves via ``data_store`` or orchestrator
    ``evidence`` (see ``resolve_data_ref``).
    """
    if operation not in OPERATIONS:
        raise StatsOperationNotAllowed(
            f"Operation {operation!r} is not allow-listed. "
            f"Allowed: {sorted(OPERATIONS)}"
        )

    df = resolve_data_ref(data_ref, evidence=evidence, data_store=data_store)
    handler = OPERATIONS[operation]
    computed = handler(df, dict(params or {}))
    return {
        "operation": operation,
        "params": dict(params or {}),
        "data_ref": data_ref,
        "result": computed,
    }
