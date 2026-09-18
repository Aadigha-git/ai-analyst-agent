"""CLI entry point for the ai-analyst-agent."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

# Allow `python -m src.cli` to import sibling packages under src/.
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import (  # noqa: E402
    RUN_SQL_TOOL_SCHEMA,
    LLMProvider,
    LLMResponse,
    get_llm_provider,
)
from orchestrator.loop import investigate  # noqa: E402
from output_formatter import (  # noqa: E402
    evidence_to_dataframe,
    export_dataframe,
    format_output,
    render_formatted,
)
from run_logger import RunLogger  # noqa: E402
from tools.schema_introspector import introspect_schema  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402


def load_trace_events(path: Path) -> list[dict[str, Any]]:
    """Load a v2-5 JSONL run trace; skip blank lines."""
    if not path.is_file():
        raise FileNotFoundError(f"Trace file not found: {path}")
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line {line_no} of {path}: {exc}"
            ) from exc
        if not isinstance(obj, dict):
            raise ValueError(f"Expected JSON object on line {line_no} of {path}")
        events.append(obj)
    return events


def _payload_summary(step_type: str, payload: Any) -> str:
    """One-line summary of a step payload (uses redaction as already stored)."""
    if not isinstance(payload, dict):
        return str(payload)[:160] if payload is not None else "(empty)"

    if step_type == "plan":
        parts = []
        if payload.get("iteration") is not None:
            parts.append(f"iteration={payload['iteration']}")
        if payload.get("tool_name"):
            parts.append(f"tool={payload['tool_name']}")
        elif payload.get("llm_kind"):
            parts.append(f"kind={payload['llm_kind']}")
        if payload.get("error"):
            parts.append(f"error={payload['error']}")
        return ", ".join(parts) or "plan"

    if step_type == "tool_call":
        tool = payload.get("tool") or "?"
        args = payload.get("arguments") or {}
        if tool == "run_sql" and isinstance(args, dict) and args.get("query"):
            q = str(args["query"]).replace("\n", " ")
            if len(q) > 80:
                q = q[:77] + "..."
            return f"{tool}: {q}"
        if tool == "ready_to_answer" and isinstance(args, dict):
            ans = str(args.get("answer") or "")[:60]
            return f"{tool}: {ans}" if ans else tool
        if tool == "needs_clarification" and isinstance(args, dict):
            q = str(args.get("clarifying_question") or "")[:60]
            return f"{tool}: {q}" if q else tool
        return tool

    if step_type == "observe":
        obs = payload.get("observation")
        if isinstance(obs, dict):
            rows = obs.get("rows")
            if isinstance(rows, dict) and rows.get("_redacted"):
                cols = rows.get("columns") or obs.get("columns") or []
                return (
                    f"tool={payload.get('tool', '?')}; "
                    f"redacted rows={rows.get('row_count', '?')} "
                    f"cols={list(cols)}"
                )
            if isinstance(rows, list):
                return (
                    f"tool={payload.get('tool', '?')}; "
                    f"rows={len(rows)} (unredacted)"
                )
            if obs.get("error"):
                return f"error={obs['error']}"
            if obs.get("answer"):
                return f"answer={str(obs['answer'])[:80]}"
            if obs.get("clarifying_question"):
                return f"clarify={str(obs['clarifying_question'])[:80]}"
        return f"tool={payload.get('tool', '?')}"

    if step_type == "verify":
        ver = payload.get("verification")
        if isinstance(ver, dict):
            consistent = ver.get("consistent")
            detail = str(ver.get("detail") or "")[:80]
            return f"consistent={consistent}" + (f"; {detail}" if detail else "")
        return "verify"

    return str(payload)[:160]


def format_replay_text(events: list[dict[str, Any]]) -> str:
    """Plain-text replay (used by tests); mirrors the Rich rendering."""
    lines: list[str] = []
    total_latency = 0
    total_tokens = 0
    total_cost = 0.0
    for i, event in enumerate(events, 1):
        step_type = str(event.get("step_type") or "?")
        latency = int(event.get("latency_ms") or 0)
        tokens = int(event.get("tokens_used") or 0)
        cost = float(event.get("cost_estimate_usd") or 0.0)
        total_latency += latency
        total_tokens += tokens
        total_cost += cost
        summary = _payload_summary(step_type, event.get("payload"))
        lines.append(f"Step {i}: {step_type}")
        lines.append(f"  summary: {summary}")
        lines.append(f"  latency_ms: {latency}")
        lines.append(f"  tokens_used: {tokens}")
        lines.append(f"  cost_estimate_usd: {cost:.8f}")
        lines.append("")
    lines.append(
        "Session summary: "
        f"total_latency_ms={total_latency}, "
        f"total_tokens={total_tokens}, "
        f"total_cost_estimate_usd={total_cost:.8f}"
    )
    return "\n".join(lines)


def render_replay(
    events: list[dict[str, Any]],
    *,
    console_: Console | None = None,
    path: Path | str | None = None,
) -> str:
    """Pretty-print a trace with Rich; return exported plain text."""
    out = console_ or Console(record=True)
    title = f"Replay: {path}" if path is not None else "Replay"
    out.print(Panel(f"[bold]{title}[/bold]  ({len(events)} steps)", expand=False))

    total_latency = 0
    total_tokens = 0
    total_cost = 0.0
    for i, event in enumerate(events, 1):
        step_type = str(event.get("step_type") or "?")
        latency = int(event.get("latency_ms") or 0)
        tokens = int(event.get("tokens_used") or 0)
        cost = float(event.get("cost_estimate_usd") or 0.0)
        total_latency += latency
        total_tokens += tokens
        total_cost += cost
        summary = _payload_summary(step_type, event.get("payload"))
        body = (
            f"[bold]{step_type}[/bold]\n"
            f"{summary}\n"
            f"latency_ms={latency}  tokens={tokens}  "
            f"cost_estimate_usd={cost:.8f}"
        )
        out.print(Panel(body, title=f"Step {i}", expand=False, padding=(0, 1)))

    out.print(
        Panel(
            "Session summary\n"
            f"total_latency_ms={total_latency}\n"
            f"total_tokens={total_tokens}\n"
            f"total_cost_estimate_usd={total_cost:.8f}",
            expand=False,
            padding=(0, 1),
        )
    )
    if hasattr(out, "export_text"):
        return out.export_text()
    return format_replay_text(events)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

app = typer.Typer(help="Agentic data-analyst agent for PostgreSQL.")
console = Console(record=True)

SYSTEM_PROMPT = (
    "You are a data analyst agent with read-only access to a PostgreSQL database. "
    "Use the run_sql tool to answer the user's question with a single SELECT query. "
    "Only generate SELECT (or WITH … SELECT) statements."
)


def _db_host_and_name() -> str:
    """Return ``host/dbname`` from the agent connection URL — never credentials."""
    raw = os.getenv("READONLY_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if not raw:
        return "(database URL not set)"
    parsed = urlparse(raw)
    host = parsed.hostname or "localhost"
    db = (parsed.path or "").lstrip("/") or "?"
    return f"{host}/{db}"


def print_cli_banner(
    provider: LLMProvider | None = None,
    *,
    console_: Console | None = None,
) -> None:
    """Print a short bordered banner on TTY stdout; skip when piped/scripted."""
    out = console_ or console
    if not out.is_terminal:
        return
    llm = provider
    provider_id = getattr(llm, "provider_id", None) or os.getenv(
        "LLM_PROVIDER", "nebius"
    )
    model = getattr(llm, "model", None) or os.getenv("LLM_MODEL") or "(default)"
    body = (
        f"[bold]AI DATA ANALYST AGENT[/bold]\n"
        f"Provider/model: {provider_id} / {model}\n"
        f"Database: {_db_host_and_name()}"
    )
    out.print(Panel(body, expand=False, padding=(0, 1)))


def run_single_step(
    question: str,
    *,
    client: LLMProvider | None = None,
) -> dict[str, Any]:
    """Minimal single-step agent: schema → LLM → one tool call → raw result."""
    schema = introspect_schema()
    llm = client or get_llm_provider()

    user_content = (
        "Database schema (JSON):\n"
        f"{json.dumps(schema, default=str)}\n\n"
        f"Question: {question}"
    )
    llm_result: LLMResponse = llm.chat(
        system_prompt=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        tools=[RUN_SQL_TOOL_SCHEMA],
    )

    if llm_result.type == "tool_call" and llm_result.tool_name:
        if llm_result.tool_name != "run_sql":
            return {
                "type": "error",
                "message": f"Unsupported tool call: {llm_result.tool_name}",
                "llm": {"usage": {"total_tokens": llm_result.tokens_used}},
            }
        query = (llm_result.tool_args or {}).get("query")
        if not isinstance(query, str) or not query.strip():
            return {
                "type": "error",
                "message": "run_sql tool call missing string 'query' argument",
                "llm": {"usage": {"total_tokens": llm_result.tokens_used}},
            }
        sql_result = run_sql(query)
        return {
            "type": "tool_result",
            "tool": "run_sql",
            "query": query,
            "result": sql_result,
            "llm": {"usage": {"total_tokens": llm_result.tokens_used}},
        }

    return {
        "type": "text",
        "text": llm_result.text or "",
        "llm": {"usage": {"total_tokens": llm_result.tokens_used}},
    }


def present_investigation(
    result: dict[str, Any],
    *,
    verbose: bool = False,
    client: LLMProvider | None = None,
    console_: Console | None = None,
    export_format: str | None = None,
    export_dir: Path | None = None,
) -> str:
    """Format and print an investigate() result; return captured display text."""
    out = console_ or Console(record=True)
    status = result.get("status")

    if status == "needs_clarification":
        out.print(
            result.get("clarifying_question") or "Could you clarify your question?"
        )
        if verbose and result.get("reason"):
            out.print(f"[dim]Reason: {result['reason']}[/dim]")
        return out.export_text() if hasattr(out, "export_text") else ""

    if status == "uncertain":
        out.print(
            result.get("message")
            or "Could not reach a confident answer within the iteration cap."
        )
        if verbose:
            out.print_json(data=result.get("trace") or [])
        return out.export_text() if hasattr(out, "export_text") else ""

    if status not in {"ok", "verification_failed"}:
        out.print_json(data=result)
        return out.export_text() if hasattr(out, "export_text") else ""

    state = result.get("state")
    evidence = list(getattr(state, "evidence", None) or [])
    answer = result.get("answer") or ""
    defaults_used = list(
        getattr(state, "defaults_used", None) or result.get("defaults_used") or []
    )
    formatted = format_output(
        answer,
        evidence,
        client=client,
        verbose=verbose,
        trace=result.get("trace"),
        defaults_used=defaults_used,
    )
    if status == "verification_failed":
        out.print(
            "[yellow]Verification flagged inconsistency; showing draft carefully.[/yellow]"
        )
        detail = (result.get("verification") or {}).get("detail")
        if detail:
            out.print(f"[dim]{detail}[/dim]")

    text = render_formatted(formatted, console=out, verbose=verbose)

    if export_format:
        df = evidence_to_dataframe(evidence)
        path = export_dataframe(
            df, export_format, output_dir=export_dir  # type: ignore[arg-type]
        )
        out.print(f"[dim]Exported evidence to {path}[/dim]")
        if hasattr(out, "export_text"):
            text = out.export_text()

    return text


@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Natural-language analytics question."),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Also show underlying SQL and tool trace (off by default).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help=(
            "Disable row-value redaction in logs/run_*.jsonl (local debugging only; "
            "never use in CI smoke)."
        ),
    ),
    export: str | None = typer.Option(
        None,
        "--export",
        help="Write supporting evidence to outputs/export_<timestamp>.{csv|xlsx}.",
        case_sensitive=False,
    ),
) -> None:
    """Investigate a question and print a narrative answer with a supporting table."""
    if export is not None and export.strip().lower() not in {"csv", "xlsx"}:
        raise typer.BadParameter("--export must be 'csv' or 'xlsx'")

    llm = get_llm_provider()
    print_cli_banner(llm)
    # Redaction is ON by default (BR-17). --no-redact is local-debug only.
    session = RunLogger(
        redact=not no_redact,
        provider=getattr(llm, "provider_id", None),
        model=getattr(llm, "model", None),
    )
    try:
        result = investigate(question, client=llm, run_logger=session)
        present_investigation(
            result,
            verbose=verbose,
            client=llm,
            export_format=export.strip().lower() if export else None,
        )
    finally:
        session.close()


@app.command("replay")
def replay(
    trace_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a logs/run_*.jsonl trace from an ask session.",
    ),
) -> None:
    """Pretty-print a structured run trace (v2-5 JSONL) without calling the LLM."""
    events = load_trace_events(trace_path)
    if not events:
        console.print(f"[yellow]No events found in {trace_path}[/yellow]")
        raise typer.Exit(code=1)
    render_replay(events, console_=console, path=trace_path)


@app.callback()
def main() -> None:
    """ai-analyst-agent CLI."""


if __name__ == "__main__":
    app()
