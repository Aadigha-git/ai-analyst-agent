# ai-analyst-agent

Self-hosted CLI (and MCP) agent that answers natural-language questions over PostgreSQL by **investigating** with SQL/stats tools, then **verifying** before it answers — not by emitting one-shot text-to-SQL.

## What's new in v2.0

- **Semantic glossary + assumption disclosure** — `config/glossary.yaml` feeds planning; glossary defaults are recorded and surfaced as explicit `Assumption:` lines (closes the v1 BQ-11 silent-default gap).
- **Expanded, versioned benchmark + CI regression gate** — `eval/benchmark_v2.json` (30 questions) keeps the v1 file as historical baseline; PRs run a 5-question live smoke subset against `smoke_baseline.json`.
- **Cross-model comparison** — `eval/eval_harness.py --compare-models` scores multiple Nebius-hosted models (`NEBIUS_COMPARE_MODELS`) into `eval/results/comparison_v2.md` (manual/nightly only; CR-2).
- **Structured tracing + replay** — each `ask` writes redacted `logs/run_*.jsonl`; `python -m src.cli replay <trace>` pretty-prints steps and session cost totals.
- **Chart recommendations + export** — rule-based `Suggested visualization: …` plus `--export {csv,xlsx}` under `outputs/`.
- **MCP server** — single tool `ask_data_question(question, database_url)` wrapping the full investigate → verify → narrative path.

## Why not single-shot text-to-SQL?

Most “ask your database” demos generate one query and trust the result. This agent is built around a bounded **plan → execute → reflect** loop: it gathers evidence across steps, can ask for clarification when the question is underspecified, and runs an **independent verification query** before presenting a narrative. Wrong guesses and unbounded retries are design failures, not features.

## Quickstart

```bash
git clone https://github.com/Aadigha-git/ai-analyst-agent.git
cd ai-analyst-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# set LLM_PROVIDER (default: nebius) and its matching API key
# (NEBIUS_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY)

docker compose up -d db   # demo DB only — skip if using your own Postgres
python -m src.cli ask "Which region had the most orders in January 2025?"
```

Add `--verbose` to print underlying SQL and the tool trace. Each `ask` also writes a structured trace under `logs/run_*.jsonl` (row values redacted by default; `--no-redact` for local debugging only). Use `--export csv` or `--export xlsx` to write the supporting evidence table under `outputs/`.

Replay a prior run without calling the LLM:

```bash
python -m src.cli replay logs/run_20260918T000001Z.jsonl
```

## Using with an MCP client

The agent can run as an [MCP](https://modelcontextprotocol.io/) server that exposes a single tool, `ask_data_question(question, database_url)`, wrapping the same investigate → verify → narrative path as the CLI (no raw SQL tools).

Standalone (stdio — preferred for local MCP clients):

```bash
python -m src.mcp_server
```

Or via Compose: `docker compose up mcp` (same image; still typically launched as a stdio subprocess by the client).

Example client config (generic MCP-capable host; adjust the Python path to your venv):

```json
{
  "mcpServers": {
    "ai-analyst-agent": {
      "command": "/absolute/path/to/ai-analyst-agent/.venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/absolute/path/to/ai-analyst-agent",
      "env": {
        "LLM_PROVIDER": "nebius",
        "NEBIUS_API_KEY": "your-key"
      }
    }
  }
}
```

Pass a **read-only** Postgres URL as `database_url` on each tool call. See [`docs/API.md`](docs/API.md#mcp-server-srcmcp_serverpy) for the full env-var list.

## Connecting to Your Own Database

`docker compose up -d db` starts a **demo/seeded** Postgres instance for local eval and Quickstart only. Intended real usage is pointing the agent at **your** PostgreSQL database.

1. Run [`scripts/create_readonly_role.sql`](scripts/create_readonly_role.sql) against **each** target database (required for every new database — not a one-time global setup).
2. Set `READONLY_DATABASE_URL` (and optionally `DATABASE_URL` for admin/setup) in `.env` to that read-only role’s connection string.
3. Author or extend [`config/glossary.yaml`](config/glossary.yaml) for that dataset’s business terms (v2 semantic glossary — seeded for the sample retail schema only).
4. Skip `docker compose up -d db` entirely if you are not using the sample data.

**Warning:** The read-only Postgres role is what enforces the “never writes” guarantee. Running the agent with a write-capable role defeats that guarantee regardless of application-level SELECT checks.

## Architecture

**HLD** — CLI / MCP → orchestrator ↔ pluggable LLM provider → tool layer → read-only Postgres; offline eval harness scores runs.

![High-level architecture](docs/images/hld_diagram.png)

**LLD** — investigation loop with iteration cap and verify-before-answer.

![Investigation loop (plan → execute → reflect → verify)](docs/images/lld_diagram.png)

More detail: [`docs/API.md`](docs/API.md), [`docs/DECISIONS.md`](docs/DECISIONS.md), [`docs/DB.md`](docs/DB.md).

## Evaluation

| Suite | Report | Notes |
| --- | --- | --- |
| **v2.0** (30 questions) | **[report_v2.md](eval/results/report_v2.md)** | `eval/benchmark_v2.json` — live score **28/30** (Nebius `Qwen/Qwen3-235B-A22B-Instruct-2507`, CR-2 re-key) |
| **v2 cross-model** | **[comparison_v2.md](eval/results/comparison_v2.md)** | `python eval/eval_harness.py --compare-models` (Nebius-hosted models; manual/nightly; not CI) |
| **v1.0 baseline** | **[10/12](eval/results/report.md)** | `eval/benchmark_questions.json` kept as historical baseline |

## CI

Pull requests run two gates:

- **Unit CI** (`.github/workflows/ci.yml` `test` job): lint + pytest against a seeded Postgres service. No live LLM calls (provider key is a placeholder).
- **Smoke regression** (`eval-smoke` job): runs `eval/run_smoke.py` on a fixed 5-question subset (`eval/smoke_subset.json`) with a **real** default-provider LLM call and fails if the score drops below `eval/results/smoke_baseline.json`. Requires repository secret `NEBIUS_API_KEY` (or the matching key if `LLM_PROVIDER` is changed). Update the baseline only manually via `python eval/run_smoke.py --update-baseline` — never from CI.

Do **not** run `--compare-models` (or legacy `--compare-providers`) in CI. The full ~30-question v2 benchmark remains a **manual / nightly** run:

```bash
python eval/eval_harness.py --benchmark eval/benchmark_v2.json
```

## Known Limitations

Pulled from [`eval/results/report_v2.md`](eval/results/report_v2.md) (**28/30**, CR-2 Nebius re-key):

- **Multi-provider abstraction vs packaged comparison** — OpenAI / Anthropic / Google remain fully supported via `LLM_PROVIDER` when you configure valid credentials. The packaged `comparison_v2.md` demo only exercises Nebius-hosted models (`--compare-models` / CR-2) so the report stays reproducible without depending on unrelated vendor billing/account state.
- **BQ-09 (multi-step join)** — can still fail to complete the Consumer×web order-count join within the iteration cap (`multi_step` / uncertain).
- **BQ-18 (verification)** — draft can name Electronics correctly then fail verification on a malformed follow-up SQL (`syntax error at or near "LIMIT"`).
- **BQ-11 disclosure quality** — structural fix shipped (glossary defaults + `Assumption:` lines); phrasing quality still depends on the model.
- **Google provider** — configured Gemini model ids may 404 depending on account/API version; OpenAI-shaped tool JSON with `additionalProperties` can also be rejected until the adapter strips unsupported fields.
- **Hosted model catalog drift** — Nebius model ids in `NEBIUS_COMPARE_MODELS` / `.env.example` should be re-checked against the current Nebius AI Studio catalog; availability changes over time.
