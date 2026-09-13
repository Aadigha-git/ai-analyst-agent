"""SQL executor tool — runs SELECT statements with row limits and timeouts."""


def run_sql(query: str) -> dict:
    """Execute a single SELECT and return capped rows, count, and timing.

    Guardrails: reject non-SELECT; enforce row limit and timeout.
    """
    raise NotImplementedError
