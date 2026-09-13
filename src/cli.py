"""CLI entry point for the ai-analyst-agent."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console

# Allow `python -m src.cli` to import sibling packages under src/.
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import RUN_SQL_TOOL_SCHEMA, LlmResult, NebiusClient  # noqa: E402
from tools.schema_introspector import introspect_schema  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

app = typer.Typer(help="Agentic data-analyst agent for PostgreSQL.")
console = Console()

SYSTEM_PROMPT = (
    "You are a data analyst agent with read-only access to a PostgreSQL database. "
    "Use the run_sql tool to answer the user's question with a single SELECT query. "
    "Only generate SELECT (or WITH … SELECT) statements."
)


def run_single_step(
    question: str,
    *,
    client: NebiusClient | None = None,
) -> dict[str, Any]:
    """Minimal single-step agent: schema → LLM → one tool call → raw result."""
    schema = introspect_schema()
    llm = client or NebiusClient()

    user_content = (
        "Database schema (JSON):\n"
        f"{json.dumps(schema, default=str)}\n\n"
        f"Question: {question}"
    )
    llm_result: LlmResult = llm.complete(
        system_prompt=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        tools=[RUN_SQL_TOOL_SCHEMA],
    )

    if llm_result.kind == "tool_call" and llm_result.tool_call is not None:
        call = llm_result.tool_call
        if call.name != "run_sql":
            return {
                "type": "error",
                "message": f"Unsupported tool call: {call.name}",
                "llm": {"usage": llm_result.usage},
            }
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {
                "type": "error",
                "message": "run_sql tool call missing string 'query' argument",
                "llm": {"usage": llm_result.usage},
            }
        sql_result = run_sql(query)
        return {
            "type": "tool_result",
            "tool": "run_sql",
            "query": query,
            "result": sql_result,
            "llm": {"usage": llm_result.usage},
        }

    return {
        "type": "text",
        "text": llm_result.text or "",
        "llm": {"usage": llm_result.usage},
    }


@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Natural-language analytics question.")
) -> None:
    """Run a single-step ask (schema + one LLM tool call); print the raw result."""
    payload = run_single_step(question)
    console.print_json(data=payload)


@app.callback()
def main() -> None:
    """ai-analyst-agent CLI."""


if __name__ == "__main__":
    app()
