# Tool / API Specifications

Internal tool contracts the Agent Orchestrator calls (Phase 3 Document 5). The system exposes no public network API in v1.

| Tool | Parameters | Returns | Guardrails |
| --- | --- | --- | --- |
| `introspect_schema()` | none (reads full accessible schema once per session) | Table/column/type metadata, foreign-key hints where present | Read-only role; cached per session to limit repeated calls |
| `run_sql(query: str)` | `query` — a single SELECT statement | `rows` (list of dicts, capped), `row_count`, `execution_time_seconds`, `truncated` (bool), `columns` | Rejects any non-SELECT **before** contacting Postgres (`SqlValidationError`); connection uses `SET TRANSACTION READ ONLY`; enforces `ROW_LIMIT` (default 500; injects `LIMIT` or truncates with `truncated=True`) and `QUERY_TIMEOUT_SECONDS` via `statement_timeout` (default 10s) |
| `run_stats(operation: str, params: object, data_ref: str)` | `operation` — one of `aggregate`, `rolling_mean`, `outlier_zscore`, `correlation`, `segment`; `params` — operation-specific args; `data_ref` — key/index into prior in-memory results (`data_store` or orchestrator `evidence`) | `{operation, params, data_ref, result}` where `result` is records or a summary object | Allow-list only via fixed `OPERATIONS` dict (ADR-004); `StatsOperationNotAllowed` for anything else; **never** `exec`/`eval`; no DB connection — optional kw-only `evidence` / `data_store` for resolution |
| `verify(claim: str, evidence_ref: str)` | `claim` — the draft conclusion; `evidence_ref` — the supporting query result(s) | `consistent: bool`, `detail: str`, `new_query_used: str` | Always issues exactly one new, independently-phrased query; never re-runs the original query verbatim |
| `format_output(answer: str, evidence: list)` | `answer` — verified conclusion; `evidence` — supporting data points | Narrative text + a simple chart/table payload | No DB or LLM access; pure presentation step |

## LLM client (`src/llm_client.py`)

Nebius AI Builder OpenAI-compatible chat completions wrapper.

| Symbol | Interface | Notes |
| --- | --- | --- |
| `NebiusClient(api_key?, base_url?, model?, session?)` | Reads `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, optional `NEBIUS_MODEL` from `.env` | Thin `requests` client; inject `session` for tests |
| `chat_completions(messages, model?, tools?, **kwargs) -> dict` | Raw POST `{base}/chat/completions` | Logs `prompt_tokens` / `completion_tokens` / `total_tokens` on every call |
| `complete(system_prompt, messages, tools?) -> LlmResult` | Prepends system message; returns normalized result | `LlmResult.kind` is `"tool_call"` or `"text"` |
| `LlmResult` | `kind`, `text?`, `tool_call?`, `usage`, `raw` | `ToolCall` has `id`, `name`, `arguments` (parsed JSON object) |
| `RUN_SQL_TOOL_SCHEMA` | OpenAI tools item: `{"type":"function","function":{name,description,parameters}}` | Shared with the minimal CLI single-step path |

### Minimal CLI single-step (`python -m src.cli ask "<question>"`)

1. `introspect_schema()` once (cached)
2. `NebiusClient.complete(...)` with schema + question and `RUN_SQL_TOOL_SCHEMA`
3. If the model returns a `run_sql` tool call, execute it and print the raw JSON result (no loop / verify yet)

## Orchestrator loop (`src/orchestrator/loop.py`)

Production plan → execute → reflect loop (Phase 3 Document 3 LLD), with POC hardening.

| Symbol | Interface | Notes |
| --- | --- | --- |
| `InvestigationState` | `question`, `schema`, `evidence`, `iterations` (+ draft / clarification fields) | Matches LLD state object |
| `investigate(question, …) -> dict` | Bounded by `MAX_ITERATIONS` (default 8) | Preloads schema; retries Nebius via `NEBIUS_MAX_RETRIES` |
| Plan tools | `run_sql`, `ready_to_answer` (ANSWER_READY), `needs_clarification` | Exactly one tool per plan turn |
| Result `status` | `ok` \| `uncertain` \| `needs_clarification` \| `verification_failed` | Cap → `uncertain`; ambiguity → clarifying question (BR-7); verify after draft |

`needs_clarification` arguments: `clarifying_question` (required), `reason` (optional).

### `run_stats` params (allow-listed)

| Operation | Required params | Result shape |
| --- | --- | --- |
| `aggregate` | `group_by` + (`agg` map **or** `column`/`func`) | list of group rows |
| `rolling_mean` | `column`, `window` | rows with `{column}_rolling_mean_{window}` |
| `outlier_zscore` | `column`; optional `threshold` (default 3) | `{threshold, mean, std, outlier_count, outliers, rows}` |
| `correlation` | `col_a`, `col_b` | `{col_a, col_b, correlation}` |
| `segment` | `group_col`, `metric_col`; optional `func` (default `mean`) | list of segment summary rows |

