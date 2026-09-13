"""Verifier — independent follow-up query to check draft conclusions."""


def verify(claim: str, evidence_ref: str) -> dict:
    """Issue one independently-phrased query and compare to the claim.

    Returns consistent, detail, and new_query_used.
    Guardrails: exactly one new query; never re-run the original verbatim.
    """
    raise NotImplementedError
