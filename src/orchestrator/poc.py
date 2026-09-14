"""Throwaway POC orchestrator: plan / execute / reflect loop + verify (WBS spike).

Replace with the full Week-2 orchestrator (WBS-4.4) when ready.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import RUN_SQL_TOOL_SCHEMA, LLMProvider, get_llm_provider  # noqa: E402
from tools.schema_introspector import (  # noqa: E402
    clear_schema_cache,
    introspect_schema,
)
from tools.sql_executor import run_sql  # noqa: E402
from tools.verifier import verify  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

INTROSPECT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "introspect_schema",
        "description": "Load table/column/type metadata and foreign-key hints (cached).",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

READY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ready_to_answer",
        "description": (
            "Call when evidence is sufficient to answer the user. "
            "Provide a concise draft answer grounded in the tool results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "Draft plain-language answer to the question.",
                }
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
}

POC_TOOLS = [INTROSPECT_TOOL_SCHEMA, RUN_SQL_TOOL_SCHEMA, READY_TOOL_SCHEMA]

SYSTEM_PROMPT = (
    "You are a data-analyst agent investigating a PostgreSQL database. "
    "Each turn, choose exactly one tool: introspect_schema, run_sql, or ready_to_answer. "
    "Prefer run_sql for factual questions once you know the schema. "
    "Use ready_to_answer only when you can state a grounded draft answer. "
    "Only SELECT / WITH…SELECT queries are allowed."
)


@dataclass
class PocState:
    question: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    prior_queries: list[str] = field(default_factory=list)
    iterations: int = 0
    schema: dict[str, Any] | None = None
    draft_answer: str | None = None


def _max_iterations() -> int:
    return int(os.getenv("MAX_ITERATIONS", "8"))


def _evidence_blob(state: PocState) -> str:
    return json.dumps(state.evidence, default=str)[:12000]


def _plan_messages(state: PocState) -> list[dict[str, Any]]:
    schema_part = (
        json.dumps(state.schema, default=str)[:8000]
        if state.schema is not None
        else "(not loaded yet — call introspect_schema)"
    )
    return [
        {
            "role": "user",
            "content": (
                f"Question: {state.question}\n\n"
                f"Schema snapshot:\n{schema_part}\n\n"
                f"Evidence so far ({len(state.evidence)} steps):\n{_evidence_blob(state)}\n\n"
                f"Iterations used: {state.iterations}/{_max_iterations()}\n"
                "Plan the next single step via a tool call."
            ),
        }
    ]


def run_poc(
    question: str,
    *,
    client: LLMProvider | None = None,
    max_iterations: int | None = None,
    reset_schema_cache: bool = True,
) -> dict[str, Any]:
    """Plan/execute loop capped by MAX_ITERATIONS, then one verify() pass."""
    if reset_schema_cache:
        clear_schema_cache()

    llm = client or get_llm_provider()
    cap = max_iterations if max_iterations is not None else _max_iterations()
    state = PocState(question=question)
    trace: list[dict[str, Any]] = []

    # POC convenience: load schema once up front so the loop can focus on SQL.
    try:
        state.schema = introspect_schema()
        state.evidence.append(
            {
                "action": "introspect_schema",
                "result": {
                    "tables": [t["name"] for t in state.schema.get("tables", [])],
                    "column_count": len(state.schema.get("columns", [])),
                    "foreign_key_count": len(state.schema.get("foreign_keys", [])),
                    "preloaded": True,
                },
            }
        )
        trace.append(
            {
                "iteration": 0,
                "action": "introspect_schema",
                "observation": state.evidence[-1]["result"],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pre-introspect failed: %s", exc)

    while state.iterations < cap:
        state.iterations += 1
        try:
            result = llm.chat(
                system_prompt=SYSTEM_PROMPT,
                messages=_plan_messages(state),
                tools=POC_TOOLS,
            )
        except Exception as exc:
            step = {
                "iteration": state.iterations,
                "action": "llm_error",
                "observation": {"error": str(exc)},
            }
            state.evidence.append(step["observation"])
            trace.append(step)
            logger.exception("LLM plan step failed at iteration %s", state.iterations)
            continue

        step = {
            "iteration": state.iterations,
            "llm_kind": result.type,
            "usage": {"total_tokens": result.tokens_used},
        }

        if result.type != "tool_call" or not result.tool_name:
            step["action"] = "text_fallback"
            step["text"] = result.text
            if result.text and state.iterations >= cap:
                state.draft_answer = result.text
                trace.append(step)
                break
            state.evidence.append({"action": "text", "content": result.text})
            trace.append(step)
            continue

        tool_name = result.tool_name
        arguments = dict(result.tool_args or {})
        step["action"] = tool_name
        step["arguments"] = arguments

        if tool_name == "introspect_schema":
            state.schema = introspect_schema()
            observation = {
                "tables": [t["name"] for t in state.schema.get("tables", [])],
                "column_count": len(state.schema.get("columns", [])),
                "foreign_key_count": len(state.schema.get("foreign_keys", [])),
            }
            state.evidence.append(
                {"action": "introspect_schema", "result": observation}
            )
            step["observation"] = observation

        elif tool_name == "run_sql":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                obs = {"error": "missing query"}
                state.evidence.append({"action": "run_sql", "error": obs["error"]})
                step["observation"] = obs
            else:
                try:
                    sql_result = run_sql(query)
                    state.prior_queries.append(query)
                    compact = {
                        "query": query,
                        "row_count": sql_result.get("row_count"),
                        "truncated": sql_result.get("truncated"),
                        "columns": sql_result.get("columns"),
                        "rows": sql_result.get("rows", [])[:25],
                    }
                    state.evidence.append({"action": "run_sql", "result": compact})
                    step["observation"] = compact
                except Exception as exc:  # noqa: BLE001
                    err = {"error": str(exc), "query": query}
                    state.evidence.append({"action": "run_sql", "error": err})
                    step["observation"] = err

        elif tool_name == "ready_to_answer":
            answer = arguments.get("answer")
            state.draft_answer = str(answer or "").strip() or None
            step["observation"] = {"answer": state.draft_answer}
            trace.append(step)
            break

        else:
            step["observation"] = {"error": f"unknown tool {tool_name}"}
            state.evidence.append(step["observation"])

        trace.append(step)

    if not state.draft_answer:
        return {
            "status": "uncertain",
            "question": question,
            "answer": None,
            "message": "Could not reach a confident answer within the iteration cap.",
            "iterations": state.iterations,
            "sql_steps": len(state.prior_queries),
            "trace": trace,
            "verification": None,
        }

    try:
        verification = verify(
            state.draft_answer,
            _evidence_blob(state),
            client=llm,
            prior_queries=state.prior_queries,
        )
    except Exception as exc:  # noqa: BLE001
        verification = {
            "consistent": False,
            "detail": f"Verifier failed: {exc}",
            "new_query_used": "",
            "verification_rows": [],
        }

    status = "ok" if verification.get("consistent") else "verification_failed"
    return {
        "status": status,
        "question": question,
        "answer": state.draft_answer,
        "iterations": state.iterations,
        "sql_steps": len(state.prior_queries),
        "trace": trace,
        "verification": verification,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    q = " ".join(sys.argv[1:]) or "How many orders are in the database?"
    out = run_poc(q)
    print(json.dumps(out, indent=2, default=str))
