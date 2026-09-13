"""Output formatter — LLM narrative + compact supporting table (BR-5)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import NebiusClient  # noqa: E402

load_dotenv()

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
    client: NebiusClient | None = None,
    verbose: bool = False,
    trace: list[Any] | None = None,
) -> dict[str, Any]:
    """Produce narrative text plus a compact supporting table payload.

    Default (verbose=False) omits raw SQL and tool traces from the return value
    so CLI default output stays user-facing. Pass verbose=True to include them.
    """
    llm = client or NebiusClient()
    table = extract_table_payload(evidence)
    evidence_for_llm = {
        "answer": answer,
        "supporting_rows": table.get("rows", [])[:10],
        "columns": table.get("columns", []),
    }
    phrased = llm.complete(
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

    payload: dict[str, Any] = {
        "narrative": narrative,
        "table": table,
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
    """Print narrative + rich table (+ optional SQL/trace) and return plain captured text."""
    console = console or Console(record=True)
    console.print(formatted.get("narrative") or "")
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
