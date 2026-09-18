"""Output formatter — LLM narrative + compact supporting table (BR-5).

Also provides lightweight chart recommendations and CSV/XLSX export (v2-8 / BR-19).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import LLMProvider, get_llm_provider  # noqa: E402

load_dotenv()

DEFAULT_EXPORT_DIR = Path(__file__).resolve().parents[1] / "outputs"

_NARRATIVE_SYSTEM = (
    "You write a short plain-language analytics narrative for an end user. "
    "Use the verified answer and supporting evidence. "
    "Do NOT include SQL, table names as schema jargon, tool names, or raw JSON. "
    "2–4 sentences max. No markdown fences."
)

_SQL_HINT = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|WITH|FROM|WHERE|JOIN|GROUP BY|ORDER BY)\b",
    re.IGNORECASE,
)

_DATE_NAME_HINT = re.compile(
    r"(^|_)(date|time|timestamp|datetime|day|month|year)(_|$)",
    re.IGNORECASE,
)


def extract_sql_queries(evidence: list[Any]) -> list[str]:
    """Collect SQL strings from evidence / tool results (for verbose mode only)."""
    queries: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            q = node.get("query")
            if isinstance(q, str) and q.strip():
                queries.append(q.strip())
            result = node.get("result")
            if isinstance(result, dict) and isinstance(result.get("query"), str):
                queries.append(result["query"].strip())
            args = node.get("arguments")
            if isinstance(args, dict) and isinstance(args.get("query"), str):
                queries.append(args["query"].strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(evidence)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = re.sub(r"\s+", " ", q.lower())
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


def extract_table_payload(evidence: list[Any], *, max_rows: int = 10) -> dict[str, Any]:
    """Build a compact table payload from the latest tabular tool result."""
    rows: list[dict[str, Any]] = []
    columns: list[str] = []

    for item in reversed(list(evidence or [])):
        if not isinstance(item, dict):
            continue
        candidate = item.get("result", item)
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("rows"), list) and candidate["rows"]:
            raw_rows = [r for r in candidate["rows"] if isinstance(r, dict)]
            if not raw_rows:
                continue
            columns = list(candidate.get("columns") or raw_rows[0].keys())
            rows = raw_rows[:max_rows]
            break
        # stats-style records
        if isinstance(candidate.get("result"), list) and candidate["result"]:
            raw_rows = [r for r in candidate["result"] if isinstance(r, dict)]
            if raw_rows:
                columns = list(raw_rows[0].keys())
                rows = raw_rows[:max_rows]
                break

    return {"columns": columns, "rows": rows}


def table_payload_to_dataframe(table_payload: dict[str, Any] | None) -> pd.DataFrame:
    """Build a DataFrame from a compact ``{columns, rows}`` table payload."""
    payload = table_payload or {}
    rows = [r for r in (payload.get("rows") or []) if isinstance(r, dict)]
    columns = list(payload.get("columns") or [])
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows)
    if columns:
        ordered = [c for c in columns if c in df.columns] + [
            c for c in df.columns if c not in columns
        ]
        df = df.reindex(columns=ordered)
    return df


def evidence_to_dataframe(evidence: list[Any], *, max_rows: int = 500) -> pd.DataFrame:
    """Extract the latest tabular evidence into a DataFrame (for charts/export)."""
    return table_payload_to_dataframe(
        extract_table_payload(evidence, max_rows=max_rows)
    )


def _is_datetime_series(series: pd.Series, name: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if not _DATE_NAME_HINT.search(str(name)):
        return False
    # Name looks like a date column — confirm with a small parse sample.
    sample = series.dropna().astype(str).head(8)
    if sample.empty:
        return True
    parsed = pd.to_datetime(sample, errors="coerce", utc=False, format="mixed")
    return bool(parsed.notna().mean() >= 0.6)


def _is_numeric_series(series: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(series):
        return False
    return bool(pd.api.types.is_numeric_dtype(series))


def _is_categorical_series(series: pd.Series, name: str) -> bool:
    if _is_datetime_series(series, name) or _is_numeric_series(series):
        return False
    if isinstance(series.dtype, pd.CategoricalDtype):
        return True
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        return True
    return False


def recommend_chart(dataframe: pd.DataFrame) -> dict[str, str]:
    """Rule-based chart suggestion for a supporting DataFrame (no rendering).

    Rules (first match wins):
    - any date/time column → ``line``
    - one categorical + one numeric → ``bar``
    - two numeric columns → ``scatter``
    - otherwise → ``table only``
    """
    if dataframe is None or dataframe.empty or len(dataframe.columns) == 0:
        return {
            "chart_type": "table only",
            "suggestion": "Suggested visualization: table only",
        }

    df = dataframe.copy()
    datetime_cols = [c for c in df.columns if _is_datetime_series(df[c], str(c))]
    numeric_cols = [
        c for c in df.columns if c not in datetime_cols and _is_numeric_series(df[c])
    ]
    categorical_cols = [
        c
        for c in df.columns
        if c not in datetime_cols
        and c not in numeric_cols
        and _is_categorical_series(df[c], str(c))
    ]

    if datetime_cols:
        date_col = datetime_cols[0]
        if numeric_cols:
            y = numeric_cols[0]
            msg = f"Suggested visualization: line chart " f"({date_col} vs. {y})"
        else:
            msg = f"Suggested visualization: line chart ({date_col})"
        return {"chart_type": "line", "suggestion": msg}

    if len(categorical_cols) == 1 and len(numeric_cols) == 1:
        x, y = categorical_cols[0], numeric_cols[0]
        return {
            "chart_type": "bar",
            "suggestion": f"Suggested visualization: bar chart ({x} vs. {y})",
        }

    if len(numeric_cols) >= 2:
        x, y = numeric_cols[0], numeric_cols[1]
        return {
            "chart_type": "scatter",
            "suggestion": f"Suggested visualization: scatter chart ({x} vs. {y})",
        }

    return {
        "chart_type": "table only",
        "suggestion": "Suggested visualization: table only",
    }


def export_dataframe(
    dataframe: pd.DataFrame,
    fmt: Literal["csv", "xlsx"],
    *,
    output_dir: Path | str | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Write ``dataframe`` to ``outputs/export_<timestamp>.{csv,xlsx}``."""
    fmt_norm = str(fmt).strip().lower()
    if fmt_norm not in {"csv", "xlsx"}:
        raise ValueError(f"Unsupported export format: {fmt!r} (use csv or xlsx)")

    directory = Path(output_dir) if output_dir is not None else DEFAULT_EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"export_{stamp}.{fmt_norm}"

    if fmt_norm == "csv":
        dataframe.to_csv(path, index=False)
    else:
        # openpyxl is required for xlsx (declared in requirements.txt).
        dataframe.to_excel(path, index=False, engine="openpyxl")
    return path


def _sanitize_narrative(text: str) -> str:
    """Drop accidental SQL-looking lines from the LLM narrative."""
    lines = []
    for line in (text or "").splitlines():
        if _SQL_HINT.search(line) and (
            " from " in line.lower() or line.strip().upper().startswith("SELECT")
        ):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned or (text or "").strip()


def format_output(
    answer: str,
    evidence: list[Any],
    *,
    client: LLMProvider | None = None,
    verbose: bool = False,
    trace: list[Any] | None = None,
    defaults_used: list[str] | None = None,
) -> dict[str, Any]:
    """Produce narrative text plus a compact supporting table payload.

    Default (verbose=False) omits raw SQL and tool traces from the return value
    so CLI default output stays user-facing. Pass verbose=True to include them.

    When ``defaults_used`` is non-empty (glossary fallbacks from planning), append
    one ``Assumption:`` line per default so silent defaults are never hidden (BR-12).

    Also attaches a rule-based ``chart_recommendation`` for the supporting table.
    """
    llm = client or get_llm_provider()
    table = extract_table_payload(evidence)
    evidence_for_llm = {
        "answer": answer,
        "supporting_rows": table.get("rows", [])[:10],
        "columns": table.get("columns", []),
    }
    phrased = llm.chat(
        system_prompt=_NARRATIVE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Verified answer:\n"
                    f"{answer}\n\n"
                    "Supporting evidence (values only, already verified):\n"
                    f"{json.dumps(evidence_for_llm, default=str)}\n\n"
                    "Write the short narrative now:"
                ),
            }
        ],
        tools=None,
    )
    narrative = _sanitize_narrative(phrased.text or answer)
    assumptions = [str(a).strip() for a in (defaults_used or []) if str(a).strip()]
    for assumption in assumptions:
        line = (
            assumption
            if assumption.lower().startswith("assumption:")
            else f"Assumption: {assumption}"
        )
        narrative = f"{narrative}\n{line}" if narrative else line

    df = table_payload_to_dataframe(table)
    chart = recommend_chart(df)

    payload: dict[str, Any] = {
        "narrative": narrative,
        "table": table,
        "defaults_used": assumptions,
        "chart_recommendation": chart,
    }
    if verbose:
        payload["sql_queries"] = extract_sql_queries(evidence)
        if isinstance(trace, list):
            payload["trace"] = trace
        else:
            payload["trace"] = list(evidence or [])
    return payload


def render_rich_table(
    table_payload: dict[str, Any], *, console: Console | None = None
) -> Table:
    """Create a Rich Table from a compact payload."""
    console = console or Console()
    columns = table_payload.get("columns") or []
    rows = table_payload.get("rows") or []
    table = Table(show_header=True, header_style="bold")
    if not columns and rows:
        columns = list(rows[0].keys())
    for col in columns:
        table.add_column(str(col))
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    return table


def render_formatted(
    formatted: dict[str, Any],
    *,
    console: Console | None = None,
    verbose: bool = False,
) -> str:
    """Print narrative + chart suggestion + rich table (+ optional SQL/trace)."""
    console = console or Console(record=True)
    console.print(formatted.get("narrative") or "")
    chart = formatted.get("chart_recommendation") or {}
    suggestion = chart.get("suggestion") if isinstance(chart, dict) else None
    if suggestion:
        console.print(f"[cyan]{suggestion}[/cyan]")

    table_payload = formatted.get("table") or {}
    if table_payload.get("rows"):
        console.print(render_rich_table(table_payload, console=console))
    elif table_payload.get("columns"):
        console.print("[dim]No supporting rows.[/dim]")

    if verbose:
        console.print("\n[bold]SQL[/bold]")
        for q in formatted.get("sql_queries") or []:
            console.print(q)
        console.print("\n[bold]Tool trace[/bold]")
        console.print_json(data=formatted.get("trace") or [])

    if hasattr(console, "export_text"):
        return console.export_text()
    return formatted.get("narrative") or ""
