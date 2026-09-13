"""CLI entry point for the ai-analyst-agent."""

from __future__ import annotations

import typer

app = typer.Typer(help="Agentic data-analyst agent for PostgreSQL.")


@app.callback()
def main() -> None:
    """ai-analyst-agent CLI (implementation coming in later tasks)."""


if __name__ == "__main__":
    app()
