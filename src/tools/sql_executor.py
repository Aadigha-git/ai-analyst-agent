"""SQL executor tool — runs SELECT statements with row limits and timeouts."""

from __future__ import annotations

import os
import re
import time
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

# Matches DML/DDL verbs we never allow through the executor.
_FORBIDDEN_KEYWORD = re.compile(
    r"\b(?:"
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|"
    r"MERGE|CALL|DO|EXECUTE|VACUUM|REINDEX|CLUSTER|COMMENT|REFRESH|"
    r"SECURITY|SET|SHOW|LISTEN|NOTIFY|LOAD|CHECKPOINT"
    r")\b",
    re.IGNORECASE,
)

_SELECT_INTO = re.compile(r"\bSELECT\b[\s\S]*?\bINTO\b", re.IGNORECASE)
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+\d+\s*(?:OFFSET\s+\d+\s*)?;?\s*$", re.IGNORECASE)


class SqlValidationError(ValueError):
    """Raised when a query fails pre-execution validation (never sent to Postgres)."""


def _strip_sql_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    lines: list[str] = []
    for line in without_block.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines)


def _mask_string_literals(sql: str) -> str:
    """Replace string literals so keyword/semicolon checks ignore their contents."""
    masked = re.sub(r"'(?:''|[^'])*'", "''", sql)
    masked = re.sub(r"\$\$.*?\$\$", "''", masked, flags=re.DOTALL)
    return masked


def validate_single_select(query: str) -> str:
    """Return a cleaned single SELECT (or SELECT CTE) or raise SqlValidationError."""
    if query is None or not str(query).strip():
        raise SqlValidationError("Query must be a non-empty single SELECT statement.")

    cleaned = _strip_sql_comments(query).strip()
    if not cleaned:
        raise SqlValidationError("Query must be a non-empty single SELECT statement.")

    masked = _mask_string_literals(cleaned)
    if masked.endswith(";"):
        masked = masked[:-1].rstrip()
        cleaned = cleaned[:-1].rstrip() if cleaned.endswith(";") else cleaned

    if ";" in masked:
        raise SqlValidationError(
            "Multiple SQL statements are not allowed; provide a single SELECT."
        )

    tokens = masked.split()
    if not tokens:
        raise SqlValidationError("Query must be a non-empty single SELECT statement.")

    first = tokens[0].lstrip("(").upper()
    if first not in {"SELECT", "WITH"}:
        raise SqlValidationError(
            f"Only SELECT statements are allowed (rejected leading keyword: {first})."
        )

    # Reject SELECT INTO (creates a table) and any CTE that wraps a write.
    if _SELECT_INTO.search(masked):
        raise SqlValidationError("SELECT INTO is not allowed.")

    if first == "WITH" and _FORBIDDEN_KEYWORD.search(masked):
        raise SqlValidationError(
            "Only SELECT CTEs are allowed; write/DDL keywords are rejected."
        )

    if first == "SELECT":
        # Allow SELECT itself; still reject embedded write verbs (e.g. weird stacks).
        remainder = masked[masked.upper().find("SELECT") + len("SELECT") :]
        if _FORBIDDEN_KEYWORD.search(remainder):
            raise SqlValidationError(
                "Query contains forbidden non-SELECT keywords and was rejected."
            )

    return cleaned


def _has_limit_clause(sql: str) -> bool:
    masked = _mask_string_literals(_strip_sql_comments(sql))
    return _LIMIT_CLAUSE.search(masked.strip().rstrip(";")) is not None


def _config() -> tuple[str, int, float]:
    conninfo = os.getenv("READONLY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not conninfo:
        raise RuntimeError(
            "READONLY_DATABASE_URL (or DATABASE_URL) must be set for run_sql."
        )
    row_limit = int(os.getenv("ROW_LIMIT", "500"))
    timeout_seconds = float(os.getenv("QUERY_TIMEOUT_SECONDS", "10"))
    return conninfo, row_limit, timeout_seconds


def run_sql(query: str) -> dict[str, Any]:
    """Execute a single SELECT and return capped rows, count, timing, and truncation.

    Guardrails:
    - Rejects any non-SELECT before contacting Postgres
    - Opens the transaction as READ ONLY
    - Enforces ROW_LIMIT (default 500) and QUERY_TIMEOUT_SECONDS (default 10)

    Returns:
        {
            "rows": list[dict],
            "row_count": int,
            "execution_time_seconds": float,
            "truncated": bool,
            "columns": list[str],
        }
    """
    cleaned = validate_single_select(query)
    conninfo, row_limit, timeout_seconds = _config()

    # Fetch one extra row when we inject LIMIT so we can set truncated accurately.
    if _has_limit_clause(cleaned):
        executable = cleaned
    else:
        executable = f"{cleaned}\nLIMIT {row_limit + 1}"

    timeout_ms = max(1, int(timeout_seconds * 1000))
    started = time.perf_counter()

    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            # SET does not accept bind parameters; timeout_ms is an int we control.
            conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            with conn.cursor() as cur:
                cur.execute(executable)
                raw_rows = cur.fetchall()
                columns = (
                    [desc.name for desc in cur.description] if cur.description else []
                )

    elapsed = time.perf_counter() - started
    truncated = len(raw_rows) > row_limit
    rows = raw_rows[:row_limit] if truncated else raw_rows

    return {
        "rows": [dict(row) for row in rows],
        "row_count": len(rows),
        "execution_time_seconds": elapsed,
        "truncated": truncated,
        "columns": columns,
    }
