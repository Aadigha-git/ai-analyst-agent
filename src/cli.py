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
from output_formatter import format_output, render_formatted  # noqa: E402
from tools.schema_introspector import introspect_schema  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402

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
    formatted = format_output(
        answer,
        evidence,
        client=client,
        verbose=verbose,
        trace=result.get("trace"),
    )
    if status == "verification_failed":
        out.print(
            "[yellow]Verification flagged inconsistency; showing draft carefully.[/yellow]"
        )
        detail = (result.get("verification") or {}).get("detail")
        if detail:
            out.print(f"[dim]{detail}[/dim]")

    return render_formatted(formatted, console=out, verbose=verbose)


@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Natural-language analytics question."),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Also show underlying SQL and tool trace (off by default).",
    ),
) -> None:
    """Investigate a question and print a narrative answer with a supporting table."""
    llm = get_llm_provider()
    print_cli_banner(llm)
    result = investigate(question, client=llm)
    present_investigation(result, verbose=verbose, client=llm)


@app.callback()
def main() -> None:
    """ai-analyst-agent CLI."""


if __name__ == "__main__":
    app()
