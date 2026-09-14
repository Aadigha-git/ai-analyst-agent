# ai-analyst-agent

Self-hosted CLI agent that answers natural-language questions over PostgreSQL by **investigating** with SQL/stats tools, then **verifying** before it answers — not by emitting one-shot text-to-SQL.

## Why not single-shot text-to-SQL?

Most “ask your database” demos generate one query and trust the result. This agent is built around a bounded **plan → execute → reflect** loop: it gathers evidence across steps, can ask for clarification when the question is underspecified, and runs an **independent verification query** before presenting a narrative. Wrong guesses and unbounded retries are design failures, not features.

## Quickstart

```bash
git clone https://github.com/Aadigha-git/ai-analyst-agent.git
cd ai-analyst-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# set NEBIUS_API_KEY in .env

docker compose up -d db
python -m src.cli ask "Which region had the most orders in January 2025?"
```

Add `--verbose` to print underlying SQL and the tool trace.

## Architecture

**HLD** — CLI → orchestrator ↔ Nebius LLM → tool layer → read-only Postgres; offline eval harness scores runs.

![High-level architecture](docs/images/hld_diagram.png)

**LLD** — investigation loop with iteration cap and verify-before-answer.

![Investigation loop (plan → execute → reflect → verify)](docs/images/lld_diagram.png)

More detail: [`docs/API.md`](docs/API.md), [`docs/DECISIONS.md`](docs/DECISIONS.md), [`docs/DB.md`](docs/DB.md).

## Evaluation

Latest benchmark: **[10/12](eval/results/report.md)** on a 12-question seeded-DB suite (multi-step + trap questions included).

## Known Limitations

Pulled from the latest eval report:

- **BQ-07 (multi-step MoM growth):** Correctly names **Central** but often omits growth magnitude (+6) and month pair in the draft answer. Drafts are not yet required to include the supporting scalar(s) the rubric checks.
- **BQ-11 (trap — Electronics revenue without time window):** Returns the correct all-time total without clarifying or stating the all-dates assumption. Open-ended totals still tend to silent defaults; full fix vs over-clarifying well-scoped questions remains an open tension.
