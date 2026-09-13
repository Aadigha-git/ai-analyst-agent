# Tool / API Specifications

Internal tool contracts the Agent Orchestrator calls (Phase 3 Document 5). The system exposes no public network API in v1.

| Tool | Parameters | Returns | Guardrails |
| --- | --- | --- | --- |
| `introspect_schema()` | none (reads full accessible schema once per session) | Table/column/type metadata, foreign-key hints where present | Read-only role; cached per session to limit repeated calls |
| `run_sql(query: str)` | `query` — a single SELECT statement | `rows` (list of dicts, capped), `row_count`, `execution_time_seconds`, `truncated` (bool), `columns` | Rejects any non-SELECT **before** contacting Postgres (`SqlValidationError`); connection uses `SET TRANSACTION READ ONLY`; enforces `ROW_LIMIT` (default 500; injects `LIMIT` or truncates with `truncated=True`) and `QUERY_TIMEOUT_SECONDS` via `statement_timeout` (default 10s) |
| `run_stats(operation: str, params: object, data_ref: str)` | `operation` — one of an allow-listed set (`aggregate`, `rolling_mean`, `outlier_zscore`, `correlation`, `segment`); `params` — operation-specific args; `data_ref` — reference to a prior query result | Computed result (table or summary statistic) | Operation must be in the allow-list; runs against already-fetched in-memory data only, never a new raw DB connection |
| `verify(claim: str, evidence_ref: str)` | `claim` — the draft conclusion; `evidence_ref` — the supporting query result(s) | `consistent: bool`, `detail: str`, `new_query_used: str` | Always issues exactly one new, independently-phrased query; never re-runs the original query verbatim |
| `format_output(answer: str, evidence: list)` | `answer` — verified conclusion; `evidence` — supporting data points | Narrative text + a simple chart/table payload | No DB or LLM access; pure presentation step |
