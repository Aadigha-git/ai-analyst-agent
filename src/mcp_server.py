"""MCP server — single tool ``ask_data_question`` (v2 ADR-009 / BR-18).

Exposes the full investigation loop (schema → glossary → plan/execute/reflect/verify
→ narrative formatter) as one MCP tool. Does **not** expose introspect_schema,
run_sql, run_stats, or verify individually.

Standalone entrypoint (stdio):
  python -m src.mcp_server

Docker Compose:
  docker compose up mcp
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parent
_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

load_dotenv(_ROOT / ".env")

from llm_client import LLMProvider, get_llm_provider  # noqa: E402
from orchestrator.loop import investigate  # noqa: E402
from output_formatter import format_output  # noqa: E402
from tools.schema_introspector import clear_schema_cache  # noqa: E402

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - import guard for clear install hint
    raise ImportError(
        "The MCP Python SDK is required for src.mcp_server. "
        "Install with: pip install 'mcp>=1.9,<2'"
    ) from exc

mcp = FastMCP(
    "ai-analyst-agent",
    instructions=(
        "Answer natural-language analytics questions over PostgreSQL via a single "
        "tool. Pass a read-only database URL with each call."
    ),
)


@contextmanager
def _use_database_url(database_url: str) -> Iterator[str]:
    """Temporarily point schema/SQL tools at ``database_url`` (read-only preferred)."""
    url = (database_url or "").strip()
    if not url:
        raise ValueError(
            "database_url must be a non-empty PostgreSQL connection string"
        )

    keys = ("READONLY_DATABASE_URL", "DATABASE_URL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["READONLY_DATABASE_URL"] = url
    os.environ["DATABASE_URL"] = url
    clear_schema_cache()
    try:
        yield url
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        clear_schema_cache()


def _narrative_from_result(
    result: dict[str, Any],
    *,
    client: LLMProvider | None,
) -> str:
    """Mirror the CLI's user-facing text path, returning narrative only (no table)."""
    status = result.get("status")

    if status == "needs_clarification":
        question = (
            result.get("clarifying_question") or "Could you clarify your question?"
        )
        reason = result.get("reason")
        if reason:
            return f"{question}\nReason: {reason}"
        return str(question)

    if status == "uncertain":
        return str(
            result.get("message")
            or "Could not reach a confident answer within the iteration cap."
        )

    if status == "error":
        return f"[error] {result.get('message') or 'unknown error'}"

    if status not in {"ok", "verification_failed"}:
        return str(result.get("answer") or result.get("message") or status)

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
        verbose=False,
        defaults_used=defaults_used,
    )
    narrative = str(formatted.get("narrative") or answer).strip()
    if status == "verification_failed":
        detail = (result.get("verification") or {}).get("detail")
        prefix = "Verification flagged inconsistency; draft answer follows."
        if detail:
            prefix = f"{prefix} ({detail})"
        return f"{prefix}\n{narrative}".strip()
    return narrative


def ask_data_question(
    question: str,
    database_url: str,
    *,
    client: LLMProvider | None = None,
) -> str:
    """Run the full investigation loop and return the final narrative text.

    This is the single MCP-facing capability: schema introspection, glossary
    loading, plan/execute/reflect/verify, and narrative formatting — the same
    path as ``python -m src.cli ask``, without exposing lower-level tools.

    Args:
        question: Natural-language analytics question.
        database_url: PostgreSQL connection URL used for this call (prefer a
            read-only role). Overrides ``READONLY_DATABASE_URL`` / ``DATABASE_URL``
            for the duration of the call.
        client: Optional LLM provider (tests / dependency injection).

    Returns:
        Plain-language narrative string (includes ``Assumption:`` lines when
        glossary defaults were applied). Clarification / uncertain statuses are
        returned as text rather than raising.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("question must be a non-empty string")

    llm = client or get_llm_provider()
    with _use_database_url(database_url):
        result = investigate(q, client=llm)
        return _narrative_from_result(result, client=llm)


@mcp.tool(name="ask_data_question")
def _mcp_ask_data_question(question: str, database_url: str) -> str:
    """Answer a natural-language question over a PostgreSQL database.

    Runs the agent's full investigate → verify → narrative pipeline against
    ``database_url``. Prefer a read-only connection string. Does not expose
    lower-level SQL/schema tools.
    """
    return ask_data_question(question, database_url)


def main() -> None:
    """Run the MCP server over stdio (default transport for local clients)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # stdio transport: MCP client spawns this process and speaks JSON-RPC on pipes.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
