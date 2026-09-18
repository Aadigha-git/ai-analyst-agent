# Tool / API Specifications

Internal tool contracts the Agent Orchestrator calls (Phase 3 Document 5). The system exposes no public network API in v1.

| Tool | Parameters | Returns | Guardrails |
| --- | --- | --- | --- |
| `introspect_schema()` | none (reads full accessible schema once per session) | Table/column/type metadata, foreign-key hints where present | Read-only role; cached per session to limit repeated calls |
| `run_sql(query: str)` | `query` — a single SELECT statement | `rows` (list of dicts, capped), `row_count`, `execution_time_seconds`, `truncated` (bool), `columns` | Rejects any non-SELECT **before** contacting Postgres (`SqlValidationError`); connection uses `SET TRANSACTION READ ONLY`; enforces `ROW_LIMIT` (default 500; injects `LIMIT` or truncates with `truncated=True`) and `QUERY_TIMEOUT_SECONDS` via `statement_timeout` (default 10s) |
| `run_stats(operation: str, params: object, data_ref: str)` | `operation` — one of `aggregate`, `rolling_mean`, `outlier_zscore`, `correlation`, `segment`; `params` — operation-specific args; `data_ref` — key/index into prior in-memory results (`data_store` or orchestrator `evidence`) | `{operation, params, data_ref, result}` where `result` is records or a summary object | Allow-list only via fixed `OPERATIONS` dict (ADR-004); `StatsOperationNotAllowed` for anything else; **never** `exec`/`eval`; no DB connection — optional kw-only `evidence` / `data_store` for resolution |
| `verify(claim: str, evidence_ref: str)` | `claim` — draft conclusion; `evidence_ref` — supporting evidence (JSON/text; used to recover original SQL) | `{consistent: bool, detail: str, new_query_used: str}` | Exactly one new query; **raises** `DuplicateVerificationQuery` if proposed SQL normalizes equal to an original; optional kw-only `prior_queries`, `client`, `sql_runner` for orchestrator/tests |
| `format_output(answer: str, evidence: list, *, defaults_used?)` | `answer` — verified conclusion; `evidence` — supporting data points / tool results; optional `defaults_used` — glossary fallback phrases from planning | `{narrative, table, defaults_used, chart_recommendation}` ; with `verbose=True` also `{sql_queries, trace}` | LLM phrases a short plain-language narrative (no SQL); when `defaults_used` is non-empty, appends `Assumption: …` line(s) (BR-12); attaches rule-based `chart_recommendation` (BR-19); compact supporting table for Rich CLI rendering; default omits raw SQL/tool trace |
| `recommend_chart(dataframe)` | pandas `DataFrame` of supporting rows | `{chart_type, suggestion}` where `chart_type` is `line` \| `bar` \| `scatter` \| `table only` | Rule-based (no rendering): date/time → line; one categorical + one numeric → bar; two numeric → scatter; else table only. Suggestion text like `Suggested visualization: bar chart (region vs. total revenue)` |
| `export_dataframe(df, fmt)` | `fmt` — `csv` \| `xlsx` | `Path` to `outputs/export_<UTC-timestamp>.{csv,xlsx}` | Uses pandas `to_csv` / `to_excel` (openpyxl for xlsx) |

## CLI (`python -m src.cli ask`)

| Flag | Default | Behavior |
| --- | --- | --- |
| `--verbose` / `-v` | off | Show underlying SQL and tool trace after the narrative + table |
| `--no-redact` | off | Disable row-value redaction in `logs/run_*.jsonl` (local debugging only; never use in CI smoke) |
| `--export {csv,xlsx}` | off | Write the supporting evidence DataFrame to `outputs/export_<timestamp>.{csv,xlsx}` |

## Run logger (`src/run_logger.py`)

Structured session traces for offline eval / replay (v2 ADR-010 / BR-15, BR-17). Each `ask` session writes `logs/run_<UTC-timestamp>.jsonl` — one JSON object per step.

| Field | Type | Notes |
| --- | --- | --- |
| `step_type` | `"plan"` \| `"tool_call"` \| `"observe"` \| `"verify"` | Emitted by the orchestrator around LLM plan turns, tool selection, tool results, and verification |
| `timestamp` | ISO-8601 UTC string | When the step was recorded |
| `latency_ms` | int | Wall-clock duration for that step |
| `tokens_used` | int | Tokens reported by the provider for the step (0 when N/A) |
| `cost_estimate_usd` | float | Approximate USD from a static per-provider/model price table (estimate only) |
| `payload` | object | Step details; **row values redacted by default** |

**Default redaction** replaces tabular `rows` with a shape placeholder:

```json
{"_redacted": true, "row_count": 2, "columns": ["region", "n"]}
```

SQL text, tool names, and non-row fields are kept. Pass `--no-redact` to persist raw row values locally; the smoke-gate CI job never enables this flag.

| Symbol | Interface | Notes |
| --- | --- | --- |
| `RunLogger(redact=True, …)` | writes JSONL under `logs/` | `redact=False` ≡ CLI `--no-redact` |
| `redact_payload(obj)` | recursive redact of `rows` lists | Used when `redact=True` |
| `estimate_cost_usd(tokens, provider?, model?)` | float | Keyed off `LLM_PROVIDER` / `LLM_MODEL` |

## LLM client (`src/llm_client.py`)

Model-agnostic provider layer (CR-1a / ADR-011). Call sites depend on ``LLMProvider``, not a Nebius-specific client.

| Symbol | Interface | Notes |
| --- | --- | --- |
| `LLMProvider` (ABC) | `chat(system_prompt, messages, tools?) -> LLMResponse` | Internal `tools` use the OpenAI tools schema from this doc; each provider translates natively |
| `LLMResponse` | `type` (`tool_call` \| `text`), `tool_name`, `tool_args`, `text`, `tokens_used` | Normalized across providers |
| `get_llm_provider()` | Reads `LLM_PROVIDER` (`nebius` \| `openai` \| `anthropic` \| `google`, default `nebius`) | Requires only the selected provider’s API key; optional `LLM_MODEL` override |
| `NebiusProvider` | OpenAI-compatible HTTP (`requests`) | Default; same behavior as v1 Nebius client |
| `OpenAIProvider` | `openai` SDK `tools` param | Needs `OPENAI_API_KEY` |
| `AnthropicProvider` | `anthropic` SDK `tools` + `input_schema`; `tool_use` blocks | Needs `ANTHROPIC_API_KEY` |
| `GoogleProvider` | `google-genai` `function_declarations`; `functionCall` parts | Needs `GOOGLE_API_KEY` |
| `RUN_SQL_TOOL_SCHEMA` | OpenAI tools item: `{"type":"function","function":{name,description,parameters}}` | Shared internal tool schema |

Legacy aliases: `NebiusClient` (= `NebiusProvider`), `LlmResult` / `ToolCall` (prefer `LLMResponse`).

### Minimal CLI single-step (`run_single_step`)

Still available for POC-style wiring tests. The default `ask` command prints a TTY-only Rich banner (provider/model + DB host/name), then runs `investigate()` → `format_output()` / Rich rendering.

## Orchestrator loop (`src/orchestrator/loop.py`)

Production plan → execute → reflect loop (Phase 3 Document 3 LLD), with POC hardening.

| Symbol | Interface | Notes |
| --- | --- | --- |
| `InvestigationState` | `question`, `schema`, `evidence`, `iterations`, `defaults_used` (+ draft / clarification fields) | Matches LLD state object; `defaults_used` records glossary fallbacks applied while planning (BR-12) |
| `investigate(question, …) -> dict` | Bounded by `MAX_ITERATIONS` (default 8) | Preloads schema; injects glossary context via `format_glossary_context()` on each plan turn; records `defaults_used`; retries LLM via `NEBIUS_MAX_RETRIES`; optional `run_logger=` for JSONL tracing |
| Plan tools | `run_sql`, `ready_to_answer` (ANSWER_READY), `needs_clarification` | Exactly one tool per plan turn |
| Result `status` | `ok` \| `uncertain` \| `needs_clarification` \| `verification_failed` | Cap → `uncertain`; ambiguity → clarifying question (BR-7); verify after draft |

`needs_clarification` arguments: `clarifying_question` (required), `reason` (optional).

## Semantic glossary (`src/glossary_loader.py`)

Config-driven business terms loaded into every orchestrator **plan** turn (v2 ADR-007 / BR-11).

| Symbol | Interface | Notes |
| --- | --- | --- |
| `config/glossary.yaml` | Map of `term` → `{definition, default_join?, …}` | Seeded for the sample retail schema; BYO DBs should add their own entries |
| `load_glossary(path?)` | `dict[str, dict]` | Missing/unreadable file → `{}` + warning (never raises for missing file) |
| `detect_defaults_used(question, glossary?)` | `list[str]` | Human-readable assumption phrases for glossary fallbacks that apply to the question |

**Context block shape** (included alongside the schema snapshot in the plan user message):

```text
Semantic glossary (canonical business terms — prefer these over guessing):
- average_order_value: mean of per-order totals, ...
  default_join: orders JOIN order_items ON ...
- default_time_window: when a question doesn't specify a date range, default to all available data
- revenue: sum of order_items.line_total
  default_join: order_items JOIN products ON ...
- …
```

### `run_stats` params (allow-listed)

| Operation | Required params | Result shape |
| --- | --- | --- |
| `aggregate` | `group_by` + (`agg` map **or** `column`/`func`) | list of group rows |
| `rolling_mean` | `column`, `window` | rows with `{column}_rolling_mean_{window}` |
| `outlier_zscore` | `column`; optional `threshold` (default 3) | `{threshold, mean, std, outlier_count, outliers, rows}` |
| `correlation` | `col_a`, `col_b` | `{col_a, col_b, correlation}` |
| `segment` | `group_col`, `metric_col`; optional `func` (default `mean`) | list of segment summary rows |

