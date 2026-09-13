"""Schema introspector tool — reads table/column/type metadata (read-only)."""


def introspect_schema() -> dict:
    """Return table/column/type metadata and foreign-key hints.

    Guardrails: read-only role; cached per session.
    """
    raise NotImplementedError
