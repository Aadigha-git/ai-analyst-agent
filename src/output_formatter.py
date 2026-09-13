"""Output formatter — narrative answer plus simple chart/table payload."""


def format_output(answer: str, evidence: list) -> dict:
    """Convert verified findings into narrative text and a presentation payload.

    Guardrails: no DB or LLM access; pure presentation.
    """
    raise NotImplementedError
