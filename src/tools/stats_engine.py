"""Stats engine — allow-listed pandas/statistical operations on in-memory data."""

ALLOW_LISTED_OPERATIONS = (
    "aggregate",
    "rolling_mean",
    "outlier_zscore",
    "correlation",
    "segment",
)


def run_stats(operation: str, params: dict, data_ref: str) -> dict:
    """Run an allow-listed stats operation against previously fetched data.

    Guardrails: operation must be allow-listed; no new DB connections.
    """
    raise NotImplementedError
