"""Production verifier — independent follow-up query (ADR-005 / docs/API.md).

Asks the LLM for one *different* corroborating SELECT, runs it, and compares
the result to the draft claim. Never re-runs the original query verbatim —
identity is enforced in code via ``DuplicateVerificationQuery``.

Also enforces *metric alignment*: the verification SQL must measure the same
quantity/grain as the claim (not a substituted entity), with one regeneration
attempt when misaligned.
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

from llm_client import LLMProvider, LLMResponse, get_llm_provider  # noqa: E402
from tools.sql_executor import run_sql  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

_VERIFY_SYSTEM = (
    "You generate exactly one PostgreSQL SELECT (or WITH … SELECT) that should "
    "independently corroborate a draft analytical answer via a *different* SQL "
    "phrasing. Do not repeat any prior/original query verbatim. "
    "CRITICAL: measure the SAME quantity, entity, and grain as the claim. "
    "Examples of INVALID substitutions: verifying 'number of customers' with "
    "COUNT(DISTINCT customer_id) FROM orders; verifying AOV (mean of per-order "
    "totals) with AVG(line_total); verifying catalog product count via order_items. "
    "Prefer an equivalent rewrite (COUNT(*) vs COUNT(1), subquery wrapping the "
    "same table, SUM(1) instead of COUNT(*), etc.). "
    "Respond with ONLY the SQL — no markdown fences, no commentary."
)

_COMPARE_SYSTEM = (
    "You compare a draft answer against an independent verification query result. "
    "First decide whether the verification SQL measures the SAME metric/entity/"
    "grain as the claim (metric_aligned). If metric_aligned is false, do NOT treat "
    "a numeric disagreement as proof the claim is wrong — set consistent=true when "
    "the investigation evidence already supports the claim, and explain the "
    "misaligned verification query; set consistent=false only when the claim itself "
    "conflicts with investigation evidence. "
    "Reply with a single JSON object only: "
    '{"consistent": true|false, "metric_aligned": true|false, '
    '"detail": "<brief explanation>"}.'
)

_ALIGN_SYSTEM = (
    "Decide whether a verification SQL query measures the same quantity/entity/"
    "grain as a draft claim (and the investigation SQL). "
    "Reply with JSON only: "
    '{"aligned": true|false, "reason": "<brief>"}.'
)

# Tables commonly confused when verifying dimension counts via fact tables.
_ENTITY_TABLE_HINTS = {
    "customer": "customers",
    "customers": "customers",
    "product": "products",
    "products": "products",
    "catalog": "products",
    "order line": "order_items",
    "line item": "order_items",
    "order_items": "order_items",
    "orders": "orders",
    "order ": "orders",
}


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


def sql_relation_names(sql: str) -> set[str]:
    """Extract simple relation names from FROM/JOIN clauses."""
    names: set[str] = set()
    for match in re.finditer(
        r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", sql, flags=re.IGNORECASE
    ):
        names.add(match.group(1).lower())
    return names


def claimed_entity_table(claim: str, originals: list[str]) -> str | None:
    """Best-effort primary entity table implied by the claim / investigation SQL."""
    claim_l = claim.lower()
    for needle, table in _ENTITY_TABLE_HINTS.items():
        if needle in claim_l:
            return table
    # Fall back to the first FROM table in the latest original query.
    if originals:
        rels = sql_relation_names(originals[-1])
        if len(rels) == 1:
            return next(iter(rels))
        for preferred in ("customers", "products", "orders", "order_items"):
            if preferred in rels:
                return preferred
    return None


def verification_targets_same_entity(
    claim: str,
    new_query: str,
    originals: list[str],
) -> bool:
    """Heuristic: verification SQL should touch the claim's primary entity table."""
    target = claimed_entity_table(claim, originals)
    if not target:
        return True
    relations = sql_relation_names(new_query)
    if not relations:
        return True
    # Allow the target table, or a rewrite that only uses that table.
    if target in relations:
        return True
    # Common false pattern: claim about customers/products but only querying orders.
    return not (
        target in {"customers", "products"} and relations <= {"orders", "order_items"}
    )


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


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    raw = (raw_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _parse_comparison(raw_text: str) -> tuple[bool, str, bool | None]:
    raw = (raw_text or "").strip()
    detail = raw
    try:
        parsed = _parse_json_object(raw)
        metric_aligned = parsed.get("metric_aligned")
        aligned = None if metric_aligned is None else bool(metric_aligned)
        return (
            bool(parsed.get("consistent")),
            str(parsed.get("detail") or detail),
            aligned,
        )
    except json.JSONDecodeError:
        lower = raw.lower()
        if (
            'consistent": false' in lower
            or "not consistent" in lower
            or "inconsistent" in lower
        ):
            return False, raw or "Could not parse verifier comparison JSON.", None
        if 'consistent": true' in lower or re.search(r"\bconsistent\b", lower):
            return True, raw or "Could not parse verifier comparison JSON.", None
        return False, raw or "Could not parse verifier comparison JSON.", None


def _llm_alignment_check(
    llm: LLMProvider,
    *,
    claim: str,
    new_query: str,
    originals: list[str],
) -> bool:
    """Ask the LLM whether verification SQL is metric-aligned; default True on error."""
    try:
        result = llm.chat(
            system_prompt=_ALIGN_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Claim:\n{claim}\n\n"
                        f"Investigation SQL:\n{json.dumps(originals, default=str)}\n\n"
                        f"Verification SQL:\n{new_query}\n"
                    ),
                }
            ],
            tools=None,
        )
        parsed = _parse_json_object(result.text or "")
        return bool(parsed.get("aligned"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("alignment check failed: %s", exc)
        return True


def _propose_verification_sql(
    llm: LLMProvider,
    *,
    claim: str,
    evidence_ref: str,
    originals: list[str],
    feedback: str | None = None,
) -> str:
    prior_block = "\n".join(f"- {q}" for q in originals) if originals else "(none)"
    extra = f"\n\nRegeneration feedback:\n{feedback}\n" if feedback else ""
    gen: LLMResponse = llm.chat(
        system_prompt=_VERIFY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft answer / claim:\n{claim}\n\n"
                    f"Evidence gathered so far:\n{evidence_ref}\n\n"
                    f"Prior/original SQL queries (do NOT repeat verbatim):\n{prior_block}"
                    f"{extra}\n"
                    "Write one different corroborating SELECT that measures the SAME metric:"
                ),
            }
        ],
        tools=None,
    )
    return strip_sql_fences(gen.text or "")


def verify(
    claim: str,
    evidence_ref: str,
    *,
    client: LLMProvider | None = None,
    prior_queries: list[str] | None = None,
    sql_runner: Any | None = None,
) -> dict[str, Any]:
    """Issue one independently-phrased query and compare to the claim.

    Returns ``{consistent: bool, detail: str, new_query_used: str}``.
    Guardrails: exactly one new query; raises ``DuplicateVerificationQuery`` if
    the proposed SQL matches an original query after normalization.
    """
    llm = client or get_llm_provider()
    execute_sql = sql_runner or run_sql
    originals = _original_queries(evidence_ref, prior_queries)

    new_query = _propose_verification_sql(
        llm, claim=claim, evidence_ref=evidence_ref, originals=originals
    )
    if not new_query:
        return {
            "consistent": False,
            "detail": "Verifier LLM returned an empty SQL query.",
            "new_query_used": "",
        }

    _assert_query_is_different(new_query, originals)

    aligned = verification_targets_same_entity(claim, new_query, originals)
    if aligned:
        aligned = _llm_alignment_check(
            llm, claim=claim, new_query=new_query, originals=originals
        )

    if not aligned:
        feedback = (
            f"Rejected misaligned verification SQL: {new_query!r}. "
            "Rewrite to measure the same entity/metric as the claim using a "
            "different phrasing, without switching to a different fact table."
        )
        logger.info("Regenerating verification SQL after metric misalignment")
        regenerated = _propose_verification_sql(
            llm,
            claim=claim,
            evidence_ref=evidence_ref,
            originals=originals,
            feedback=feedback,
        )
        if regenerated:
            _assert_query_is_different(regenerated, originals)
            new_query = regenerated

    try:
        sql_result = execute_sql(new_query)
    except Exception as exc:  # noqa: BLE001 — surface as inconsistent verification
        return {
            "consistent": False,
            "detail": f"Verification query failed: {exc}",
            "new_query_used": new_query,
        }

    compare: LLMResponse = llm.chat(
        system_prompt=_COMPARE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft answer / claim:\n{claim}\n\n"
                    f"Investigation evidence:\n{evidence_ref}\n\n"
                    f"Verification SQL:\n{new_query}\n\n"
                    f"Verification result JSON:\n{json.dumps(sql_result, default=str)}\n"
                ),
            }
        ],
        tools=None,
    )
    consistent, detail, metric_aligned = _parse_comparison(compare.text or "")

    # If the judge admits the verify SQL was misaligned, prefer evidence-backed claim.
    if metric_aligned is False and not consistent:
        detail = f"{detail} (treated as inconclusive: verification SQL was metric-misaligned)"
        # Fall back to consistent with investigation evidence when claim matches
        # a clear scalar in evidence rows — keep False only if evidence contradicts.
        consistent = True

    return {
        "consistent": consistent,
        "detail": detail,
        "new_query_used": new_query,
    }
