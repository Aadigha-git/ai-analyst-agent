"""Production verifier — independent follow-up query (ADR-005 / docs/API.md).

Asks the LLM for one *different* corroborating SELECT, runs it, and compares
the result to the draft claim. Never re-runs the original query verbatim —
identity is enforced in code via ``DuplicateVerificationQuery``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from llm_client import LlmResult, NebiusClient  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

_VERIFY_SYSTEM = (
    "You generate exactly one PostgreSQL SELECT (or WITH … SELECT) that should "
    "independently corroborate a draft analytical answer via a *different* query "
    "path than the investigation so far. Do not repeat any prior/original query "
    "verbatim. Respond with ONLY the SQL — no markdown fences, no commentary."
)

_COMPARE_SYSTEM = (
    "You compare a draft answer against an independent verification query result. "
    "Reply with a single JSON object only: "
    '{"consistent": true|false, "detail": "<brief explanation>"}.'
)


class DuplicateVerificationQuery(ValueError):
    """Raised when the proposed verification SQL matches an original query."""


def normalize_sql(sql: str) -> str:
    """Normalize SQL for identity comparison (case/whitespace/trailing semicolon)."""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def strip_sql_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip().rstrip(";")


def _extract_queries_from_evidence_ref(evidence_ref: str) -> list[str]:
    """Pull original SQL strings out of an evidence JSON blob or plain text."""
    found: list[str] = []
    text = (evidence_ref or "").strip()
    if not text:
        return found
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return found

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("query"), str):
                found.append(node["query"])
            result = node.get("result")
            if isinstance(result, dict) and isinstance(result.get("query"), str):
                found.append(result["query"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def _original_queries(
    evidence_ref: str,
    prior_queries: list[str] | None,
) -> list[str]:
    originals: list[str] = []
    for q in prior_queries or []:
        if isinstance(q, str) and q.strip():
            originals.append(q)
    originals.extend(_extract_queries_from_evidence_ref(evidence_ref))
    # Preserve order, drop exact-normalize duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for q in originals:
        key = normalize_sql(q)
        if key and key not in seen:
            seen.add(key)
            deduped.append(q)
    return deduped


def _assert_query_is_different(new_query: str, originals: list[str]) -> None:
    """Raise if the verification query is identical to any original (ADR-005)."""
    new_norm = normalize_sql(new_query)
    for original in originals:
        if new_norm == normalize_sql(original):
            raise DuplicateVerificationQuery(
                "Verification query must differ from the original; "
                f"got identical SQL: {new_query!r}"
            )


def _parse_comparison(raw_text: str) -> tuple[bool, str]:
    raw = (raw_text or "").strip()
    detail = raw
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return bool(parsed.get("consistent")), str(parsed.get("detail") or detail)
    except json.JSONDecodeError:
        lower = raw.lower()
        if (
            'consistent": false' in lower
            or "not consistent" in lower
            or "inconsistent" in lower
        ):
            return False, raw or "Could not parse verifier comparison JSON."
        if 'consistent": true' in lower or re.search(r"\bconsistent\b", lower):
            return True, raw or "Could not parse verifier comparison JSON."
        return False, raw or "Could not parse verifier comparison JSON."


def verify(
    claim: str,
    evidence_ref: str,
    *,
    client: NebiusClient | None = None,
    prior_queries: list[str] | None = None,
    sql_runner: Any | None = None,
) -> dict[str, Any]:
    """Issue one independently-phrased query and compare to the claim.

    Returns ``{consistent: bool, detail: str, new_query_used: str}``.
    Guardrails: exactly one new query; raises ``DuplicateVerificationQuery`` if
    the proposed SQL matches an original query after normalization.
    """
    llm = client or NebiusClient()
    execute_sql = sql_runner or run_sql
    originals = _original_queries(evidence_ref, prior_queries)
    prior_block = "\n".join(f"- {q}" for q in originals) if originals else "(none)"

    gen: LlmResult = llm.complete(
        system_prompt=_VERIFY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft answer / claim:\n{claim}\n\n"
                    f"Evidence gathered so far:\n{evidence_ref}\n\n"
                    f"Prior/original SQL queries (do NOT repeat verbatim):\n{prior_block}\n\n"
                    "Write one different corroborating SELECT:"
                ),
            }
        ],
        tools=None,
    )
    new_query = strip_sql_fences(gen.text or "")
    if not new_query:
        return {
            "consistent": False,
            "detail": "Verifier LLM returned an empty SQL query.",
            "new_query_used": "",
        }

    _assert_query_is_different(new_query, originals)

    try:
        sql_result = execute_sql(new_query)
    except Exception as exc:  # noqa: BLE001 — surface as inconsistent verification
        return {
            "consistent": False,
            "detail": f"Verification query failed: {exc}",
            "new_query_used": new_query,
        }

    compare: LlmResult = llm.complete(
        system_prompt=_COMPARE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft answer / claim:\n{claim}\n\n"
                    f"Verification SQL:\n{new_query}\n\n"
                    f"Verification result JSON:\n{json.dumps(sql_result, default=str)}\n"
                ),
            }
        ],
        tools=None,
    )
    consistent, detail = _parse_comparison(compare.text or "")

    return {
        "consistent": consistent,
        "detail": detail,
        "new_query_used": new_query,
    }
