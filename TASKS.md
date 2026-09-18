# Tasks

| ID | Description | Requirement ID | Owner | Estimate | Status |
| --- | --- | --- | --- | --- | --- |
| WBS-3.1 | Verify Postgres read-only role enforcement (SELECT ok; INSERT/UPDATE/DROP denied) | BR-6 | — | S | Done |
| WBS-4.2 | SQL executor with SELECT-only validation, read-only txn, ROW_LIMIT, statement_timeout | BR-2, BR-6 | — | M | Done |
| WBS-4.7 | Nebius LLM client + minimal single-step CLI (`ask`) wiring schema → tool call → run_sql | BR-10 | — | M | Done |
| POC | Throwaway plan/execute loop + verify spike (`src/orchestrator/poc.py`) against 3 sample-DB questions | BR-3, BR-4 | — | M | Done |
| WBS-4.4 | Production plan/execute/reflect loop (`InvestigationState`, MAX_ITERATIONS, NEEDS_CLARIFICATION) | BR-3, BR-7 | — | M | Done |
| WBS-4.3 | Sandboxed stats engine (`run_stats` allow-list; no exec/eval) | BR-3, BR-5 | — | M | Done |
| WBS-4.5 | Production self-verification (`verify`; reject identical SQL in code) | BR-4 | — | M | Done |
| WBS-4.6 | Narrative output formatter + CLI `--verbose` SQL/trace | BR-5 | — | M | Done |
| WBS-5.1 | Evaluation benchmark question set (12 seeded-DB questions with rubrics) | BR-8 | — | M | Done |
| WBS-5.2 | Evaluation harness + first benchmark run (`eval/results/report.md`) | BR-8 | — | M | Done |
| WBS-5.3 | Address benchmark failure modes (metric-aligned verify, clarification policy, grader) | BR-4, BR-7, BR-8 | — | M | Done |
| WBS-6.1 | Final README (one-liner, investigation framing, Quickstart, eval score, known limits) | BR-5, BR-8 | — | S | Done |
| WBS-6.2 | Architecture diagrams in-repo (`docs/images/` HLD + LLD; embedded in README) | — | — | S | Done |
| CR-1 | Post-v1.0.0 change request: multi-provider LLM, BYO-DB docs, CLI banner | (see sub-items) | — | L | Done |
| CR-1a | Model-agnostic LLMProvider (Nebius/OpenAI/Anthropic/Google) + factory + tests | BR-10 (amended) | — | L | Done |
| CR-1b | README + DB.md: bring-your-own-database clarity; Quickstart multi-provider env | BR-2, BR-9 | — | S | Done |
| CR-1c | CLI Rich banner (provider/model + DB host/name; TTY-only) | UX | — | S | Done |

## v2.0

| ID | Description | Requirement ID | Owner | Estimate | Status |
| --- | --- | --- | --- | --- | --- |
| v2-1 | Semantic glossary format + loader (`config/glossary.yaml` → planning context) | BR-11 | — | M | Done |
| v2-2 | Assumption-disclosure behavior in narrative output | BR-12 | — | M | Done |
| v2-3 | Expanded, versioned benchmark suite (~30 questions) | BR-13 | — | L | Done |
| v2-4 | CI smoke-subset regression gate | BR-13 | — | M | Done |
| v2-5 | Structured tracing + redaction | BR-15, BR-17 | — | L | Done |
| v2-6 | Local run replay command | BR-16 | — | M | Done |
| v2-7 | Cross-model evaluation comparison | BR-14 | — | M | Done |
| v2-8 | Chart recommendation + CSV/XLSX export | BR-19 | — | M | Done |
| v2-9 | MCP server (`ask_data_question` tool) | BR-18 | — | L | Done |
| v2-10 | MCP guardrail-parity test | BR-18 | — | M | Done |
| v2-11 | README / docs update for v2.0 | — | — | S | Done |
| v2-12 | Final polish, tag v2.0.0 | All | — | M | Planned |
