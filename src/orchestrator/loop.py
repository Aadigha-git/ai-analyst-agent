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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from glossary_loader import detect_defaults_used, format_glossary_context  # noqa: E402
from llm_client import (  # noqa: E402
    RUN_SQL_TOOL_SCHEMA,
    LLMProvider,
    LLMResponse,
    get_llm_provider,
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
            "(maps to ANSWER_READY in the LLD). Include the key numeric result "
            "and ranking basis (e.g. region plus growth amount), not only the label."
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
            "Call ONLY when a critical metric or entity definition is missing and "
            "no reasonable default exists (e.g. 'how are sales' with no metric). "
            "Do NOT use for: defaultable time windows over all available data; "
            "SQL NULL/empty aggregates (fix or interpret as zero); or questions "
            "that already name the metric (count, sum of line_total, MoM growth)."
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
    "When a semantic glossary is present, use it for ambiguous business terms "
    "(revenue, AOV, time windows, etc.) instead of inventing definitions. "
    "Each turn, choose exactly one tool: run_sql, ready_to_answer, or needs_clarification. "
    "Clarification policy (BR-7): ask only when the metric/entity itself is undefined "
    "(e.g. vague 'sales performance') or a revenue/total question has no time scope AND "
    "you will not explicitly state an all-available-dates assumption. "
    "Do NOT ask for a time range when the question asks for the largest/best over the "
    "data (use all available dates). "
    "If a query returns NULL/empty for SUM/COUNT, treat SUM of no rows as 0 or fix the "
    "SQL (joins/filters) — never ask the user what to do with a null result. "
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
    defaults_used: list[str] = field(default_factory=list)


def _record_glossary_defaults(state: InvestigationState) -> None:
    """Record glossary defaults consulted for this question (BR-12)."""
    for item in detect_defaults_used(state.question):
        if item not in state.defaults_used:
            state.defaults_used.append(item)


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
    _record_glossary_defaults(state)
    glossary_part = format_glossary_context()
    glossary_block = f"{glossary_part}\n\n" if glossary_part else ""
    return [
        {
            "role": "user",
            "content": (
                f"Question: {state.question}\n\n"
                f"Schema snapshot:\n{schema_part}\n\n"
                f"{glossary_block}"
                f"Evidence so far ({len(state.evidence)} steps):\n{_evidence_blob(state)}\n\n"
                f"Iterations used: {state.iterations}/{cap}\n"
                "Plan the next single step via a tool call."
            ),
        }
    ]


def _complete_with_retries(
    client: LLMProvider,
    *,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> LLMResponse:
    """Retry transient LLM failures (POC hardening)."""
    attempts = max(1, _llm_retries())
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.chat(
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


def _execute_run_sql(
    state: InvestigationState, arguments: dict[str, Any]
) -> dict[str, Any]:
    query = arguments.get("query")
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
    client: LLMProvider | None = None,
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

    llm = client or get_llm_provider()
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
            "llm_kind": result.type,
            "usage": {"total_tokens": result.tokens_used},
        }

        if result.type != "tool_call" or not result.tool_name:
            # Reflect: free text is not a terminal answer unless we are out of room.
            step["action"] = "text_fallback"
            step["text"] = result.text
            state.evidence.append({"action": "text", "content": result.text})
            trace.append(step)
            last_action = "text_fallback"
            continue

        tool_name = result.tool_name
        arguments = dict(result.tool_args or {})
        step["action"] = tool_name
        step["arguments"] = arguments
        last_action = tool_name

        if tool_name == "needs_clarification":
            state.clarifying_question = str(
                arguments.get("clarifying_question") or ""
            ).strip()
            state.clarification_reason = (
                str(arguments.get("reason") or "").strip() or None
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

        if tool_name == "ready_to_answer":
            state.draft_answer = str(arguments.get("answer") or "").strip() or None
            _record_glossary_defaults(state)
            step["observation"] = {
                "answer": state.draft_answer,
                "defaults_used": list(state.defaults_used),
            }
            trace.append(step)
            break

        if tool_name == "run_sql":
            step["observation"] = _execute_run_sql(state, arguments)
            trace.append(step)
            continue

        step["observation"] = {"error": f"unknown tool {tool_name}"}
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
                "defaults_used": list(state.defaults_used),
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
        "defaults_used": list(state.defaults_used),
        "state": state,
    }
