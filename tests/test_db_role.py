"""Verify the Postgres analyst_readonly role is SELECT-only (BR-6)."""

from __future__ import annotations

import os

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import errors as pg_errors

load_dotenv()


@pytest.fixture(scope="module")
def readonly_conninfo() -> str:
    conninfo = os.getenv("READONLY_DATABASE_URL")
    if not conninfo:
        pytest.fail(
            "READONLY_DATABASE_URL is not set; copy .env.example to .env "
            "and point it at the analyst_readonly role credentials."
        )
    return conninfo


@pytest.fixture
def readonly_conn(readonly_conninfo: str):
    with psycopg.connect(readonly_conninfo) as conn:
        yield conn


def test_readonly_role_select_succeeds(readonly_conn: psycopg.Connection) -> None:
    with readonly_conn.cursor() as cur:
        cur.execute("SELECT order_id FROM orders LIMIT 1")
        row = cur.fetchone()
    assert row is not None


def test_readonly_role_insert_raises_permission_error(
    readonly_conn: psycopg.Connection,
) -> None:
    with (
        readonly_conn.cursor() as cur,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        cur.execute(
            "INSERT INTO orders (customer_id, order_date, channel) "
            "VALUES (1, CURRENT_DATE, 'web')"
        )


def test_readonly_role_update_raises_permission_error(
    readonly_conn: psycopg.Connection,
) -> None:
    with (
        readonly_conn.cursor() as cur,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        cur.execute("UPDATE orders SET channel = 'web' WHERE order_id = 1")


def test_readonly_role_drop_table_raises_permission_error(
    readonly_conn: psycopg.Connection,
) -> None:
    with (
        readonly_conn.cursor() as cur,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        cur.execute("DROP TABLE orders")
