"""Verifier — independent follow-up query to check draft conclusions (POC spike)."""

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

from llm_client import NebiusClient  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

_VERIFY_SYSTEM = (
    "You generate exactly one PostgreSQL SELECT (or WITH … SELECT) that should "
    "corroborate a draft analytical answer via a *different* query path than the "
    "investigation so far. Do not repeat any prior query verbatim. "
    "Respond with ONLY the SQL — no markdown fences, no commentary."
)

_COMPARE_SYSTEM = (
    "You compare a draft answer against an independent verification query result. "
    "Reply with a single JSON object only: "
    '{"consistent": true|false, "detail": "<brief explanation>"}.'
)


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def _strip_sql_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip().rstrip(";")


def verify(
    claim: str,
    evidence_ref: str,
    *,
    client: NebiusClient | None = None,
    prior_queries: list[str] | None = None,
) -> dict[str, Any]:
    """Issue one independently-phrased query and compare to the claim.

    Returns consistent, detail, and new_query_used.
    Guardrails: exactly one new query; never re-run the original verbatim.
    """
    llm = client or NebiusClient()
    priors = prior_queries or []
    prior_block = "\n".join(f"- {q}" for q in priors) if priors else "(none)"

    gen = llm.complete(
        system_prompt=_VERIFY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft answer / claim:\n{claim}\n\n"
                    f"Evidence gathered so far:\n{evidence_ref}\n\n"
                    f"Prior SQL queries (do NOT repeat verbatim):\n{prior_block}\n\n"
                    "Write one different corroborating SELECT:"
                ),
            }
        ],
        tools=None,
    )
    new_query = _strip_sql_fences(gen.text or "")
    if not new_query:
        return {
            "consistent": False,
            "detail": "Verifier LLM returned an empty SQL query.",
            "new_query_used": "",
            "verification_rows": [],
        }

    if any(_normalize_sql(new_query) == _normalize_sql(p) for p in priors):
        return {
            "consistent": False,
            "detail": "Verifier attempted to re-run a prior query verbatim; rejected.",
            "new_query_used": new_query,
            "verification_rows": [],
        }

    try:
        sql_result = run_sql(new_query)
    except Exception as exc:  # noqa: BLE001 — surface tool failure into verify outcome
        return {
            "consistent": False,
            "detail": f"Verification query failed: {exc}",
            "new_query_used": new_query,
            "verification_rows": [],
        }

    compare = llm.complete(
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
    raw = (compare.text or "").strip()
    consistent = False
    detail = raw
    try:
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        consistent = bool(parsed.get("consistent"))
        detail = str(parsed.get("detail") or detail)
    except json.JSONDecodeError:
        # Fallback: look for an explicit true/false signal in prose.
        lower = raw.lower()
        if (
            'consistent": false' in lower
            or "not consistent" in lower
            or "inconsistent" in lower
        ):
            consistent = False
        elif 'consistent": true' in lower or "consistent with" in lower:
            consistent = True
        detail = raw or "Could not parse verifier comparison JSON."

    return {
        "consistent": consistent,
        "detail": detail,
        "new_query_used": new_query,
        "verification_rows": sql_result.get("rows", []),
    }
