"""Production plan → execute → reflect loop (Phase 3 Document 3 LLD).

Incorporates POC spike hardening (docs/DECISIONS.md — Go with hardening):
preload schema outside the LLM loop, bound by MAX_ITERATIONS, retry transient
Nebius failures, and stop with NEEDS_CLARIFICATION rather than guessing (BR-7).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import (  # noqa: E402
    RUN_SQL_TOOL_SCHEMA,
    LlmResult,
    NebiusClient,
    ToolCall,
)
from tools.schema_introspector import (  # noqa: E402
    clear_schema_cache,
    introspect_schema,
)
from tools.sql_executor import run_sql  # noqa: E402
from tools.verifier import verify  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

READY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ready_to_answer",
        "description": (
            "Call when evidence is sufficient for a grounded draft answer "
            "(maps to ANSWER_READY in the LLD)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "Draft plain-language answer grounded in evidence.",
                }
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
}

CLARIFY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "needs_clarification",
        "description": (
            "Call when the question is ambiguous (missing metric, unclear time range, "
            "undefined segment, etc.). Do not guess — ask the user one clarifying question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "clarifying_question": {
                    "type": "string",
                    "description": "A single clarifying question for the user.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the original question cannot be answered yet.",
                },
            },
            "required": ["clarifying_question"],
            "additionalProperties": False,
        },
    },
}

LOOP_TOOLS = [RUN_SQL_TOOL_SCHEMA, READY_TOOL_SCHEMA, CLARIFY_TOOL_SCHEMA]

SYSTEM_PROMPT = (
    "You are a data-analyst agent investigating a PostgreSQL database. "
    "The schema is already loaded and provided in context. "
    "Each turn, choose exactly one tool: run_sql, ready_to_answer, or needs_clarification. "
    "Use needs_clarification when the question is ambiguous (missing metric, time range, "
    "filter, or definition) — never invent assumptions. "
    "Use ready_to_answer only with a draft grounded in tool evidence. "
    "Only SELECT / WITH…SELECT queries are allowed."
)


@dataclass
class InvestigationState:
    """LLD InvestigationState: question, schema, evidence, iterations."""

    question: str
    schema: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    prior_queries: list[str] = field(default_factory=list)
    draft_answer: str | None = None
    clarifying_question: str | None = None
    clarification_reason: str | None = None


def _max_iterations() -> int:
    return int(os.getenv("MAX_ITERATIONS", "8"))


def _llm_retries() -> int:
    return int(os.getenv("NEBIUS_MAX_RETRIES", "3"))


def _evidence_blob(state: InvestigationState) -> str:
    return json.dumps(state.evidence, default=str)[:12000]


def _plan_messages(state: InvestigationState, cap: int) -> list[dict[str, Any]]:
    schema_part = (
        json.dumps(state.schema, default=str)[:8000]
        if state.schema is not None
        else "(schema unavailable)"
    )
    return [
        {
            "role": "user",
            "content": (
                f"Question: {state.question}\n\n"
                f"Schema snapshot:\n{schema_part}\n\n"
                f"Evidence so far ({len(state.evidence)} steps):\n{_evidence_blob(state)}\n\n"
                f"Iterations used: {state.iterations}/{cap}\n"
                "Plan the next single step via a tool call."
            ),
        }
    ]


def _complete_with_retries(
    client: NebiusClient,
    *,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> LlmResult:
    """Retry transient Nebius failures (POC hardening)."""
    attempts = max(1, _llm_retries())
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.complete(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001 — retry then surface
            last_exc = exc
            logger.warning("LLM plan attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    assert last_exc is not None
    raise last_exc


def _execute_run_sql(state: InvestigationState, call: ToolCall) -> dict[str, Any]:
    query = call.arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        observation = {"error": "missing query"}
        state.evidence.append({"action": "run_sql", "error": observation["error"]})
        return observation
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
        return compact
    except Exception as exc:  # noqa: BLE001
        err = {"error": str(exc), "query": query}
        state.evidence.append({"action": "run_sql", "error": err})
        return err


def investigate(
    question: str,
    *,
    client: NebiusClient | None = None,
    max_iterations: int | None = None,
    reset_schema_cache: bool = True,
    preload_schema: bool = True,
    run_verification: bool = True,
    schema_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan → execute → reflect loop bounded by MAX_ITERATIONS.

    Returns a result dict with ``status`` one of:
    ``ok``, ``uncertain``, ``needs_clarification``, ``verification_failed``.
    """
    if reset_schema_cache:
        clear_schema_cache()

    llm = client or NebiusClient()
    cap = max_iterations if max_iterations is not None else _max_iterations()
    state = InvestigationState(question=question)
    trace: list[dict[str, Any]] = []
    load_schema = schema_loader or introspect_schema

    if preload_schema:
        try:
            state.schema = load_schema()
            preload_obs = {
                "tables": [
                    t.get("name") for t in (state.schema or {}).get("tables", [])
                ],
                "column_count": len((state.schema or {}).get("columns", [])),
                "preloaded": True,
            }
            state.evidence.append(
                {"action": "introspect_schema", "result": preload_obs}
            )
            trace.append(
                {
                    "iteration": 0,
                    "action": "introspect_schema",
                    "observation": preload_obs,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("schema preload failed: %s", exc)
            state.evidence.append({"action": "introspect_schema", "error": str(exc)})

    last_action: str | None = None

    while state.iterations < cap:
        state.iterations += 1
        try:
            result = _complete_with_retries(
                llm,
                system_prompt=SYSTEM_PROMPT,
                messages=_plan_messages(state, cap),
                tools=LOOP_TOOLS,
            )
        except Exception as exc:  # noqa: BLE001
            step = {
                "iteration": state.iterations,
                "action": "llm_error",
                "observation": {"error": str(exc)},
            }
            state.evidence.append(step["observation"])
            trace.append(step)
            last_action = "llm_error"
            continue

        step: dict[str, Any] = {
            "iteration": state.iterations,
            "llm_kind": result.kind,
            "usage": result.usage,
        }

        if result.kind != "tool_call" or result.tool_call is None:
            # Reflect: free text is not a terminal answer unless we are out of room.
            step["action"] = "text_fallback"
            step["text"] = result.text
            state.evidence.append({"action": "text", "content": result.text})
            trace.append(step)
            last_action = "text_fallback"
            continue

        call = result.tool_call
        step["action"] = call.name
        step["arguments"] = call.arguments
        last_action = call.name

        if call.name == "needs_clarification":
            state.clarifying_question = str(
                call.arguments.get("clarifying_question") or ""
            ).strip()
            state.clarification_reason = (
                str(call.arguments.get("reason") or "").strip() or None
            )
            step["observation"] = {
                "clarifying_question": state.clarifying_question,
                "reason": state.clarification_reason,
            }
            trace.append(step)
            return {
                "status": "needs_clarification",
                "question": question,
                "clarifying_question": state.clarifying_question
                or "Could you clarify what you want measured?",
                "reason": state.clarification_reason,
                "iterations": state.iterations,
                "trace": trace,
                "state": state,
            }

        if call.name == "ready_to_answer":
            state.draft_answer = str(call.arguments.get("answer") or "").strip() or None
            step["observation"] = {"answer": state.draft_answer}
            trace.append(step)
            break

        if call.name == "run_sql":
            step["observation"] = _execute_run_sql(state, call)
            trace.append(step)
            continue

        step["observation"] = {"error": f"unknown tool {call.name}"}
        state.evidence.append(step["observation"])
        trace.append(step)

    if not state.draft_answer or last_action != "ready_to_answer":
        return {
            "status": "uncertain",
            "question": question,
            "answer": None,
            "message": "Could not reach a confident answer within the iteration cap.",
            "iterations": state.iterations,
            "sql_steps": len(state.prior_queries),
            "trace": trace,
            "verification": None,
            "state": state,
        }

    verification = None
    if run_verification:
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
            }

        if not verification.get("consistent"):
            return {
                "status": "verification_failed",
                "question": question,
                "answer": state.draft_answer,
                "iterations": state.iterations,
                "sql_steps": len(state.prior_queries),
                "trace": trace,
                "verification": verification,
                "state": state,
            }

    return {
        "status": "ok",
        "question": question,
        "answer": state.draft_answer,
        "iterations": state.iterations,
        "sql_steps": len(state.prior_queries),
        "trace": trace,
        "verification": verification,
        "state": state,
    }
