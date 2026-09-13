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
