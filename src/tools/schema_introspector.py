"""Schema introspector tool — reads table/column/type metadata (read-only)."""

from __future__ import annotations

import os
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()

_schema_cache: dict[str, Any] | None = None


def _conninfo() -> str:
    conninfo = os.getenv("READONLY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not conninfo:
        raise RuntimeError(
            "READONLY_DATABASE_URL (or DATABASE_URL) must be set for introspect_schema."
        )
    return conninfo


def clear_schema_cache() -> None:
    """Reset the per-process schema cache (tests / new sessions)."""
    global _schema_cache
    _schema_cache = None


def introspect_schema(*, use_cache: bool = True) -> dict[str, Any]:
    """Return table/column/type metadata and foreign-key hints.

    Guardrails: read-only role; cached per session to limit repeated calls.
    """
    global _schema_cache
    if use_cache and _schema_cache is not None:
        return _schema_cache

    tables_sql = """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
    """
    columns_sql = """
        SELECT table_schema, table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, ordinal_position
    """
    fks_sql = """
        SELECT
            tc.table_schema,
            tc.table_name,
            kcu.column_name,
            ccu.table_schema AS foreign_table_schema,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tc.table_schema, tc.table_name, kcu.column_name
    """

    with psycopg.connect(_conninfo()) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(tables_sql)
            tables = [{"schema": r[0], "name": r[1]} for r in cur.fetchall()]
            cur.execute(columns_sql)
            columns = [
                {
                    "schema": r[0],
                    "table": r[1],
                    "name": r[2],
                    "data_type": r[3],
                    "is_nullable": r[4],
                }
                for r in cur.fetchall()
            ]
            cur.execute(fks_sql)
            foreign_keys = [
                {
                    "schema": r[0],
                    "table": r[1],
                    "column": r[2],
                    "foreign_schema": r[3],
                    "foreign_table": r[4],
                    "foreign_column": r[5],
                }
                for r in cur.fetchall()
            ]

    result = {
        "tables": tables,
        "columns": columns,
        "foreign_keys": foreign_keys,
    }
    if use_cache:
        _schema_cache = result
    return result
