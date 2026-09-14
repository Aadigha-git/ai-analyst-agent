# Architecture Decision Records

ADRs from Phase 3 Document 4 (unabridged).

## ADR-001: Restrict v1 to PostgreSQL only

**Status:** Accepted

**Context:** Supporting multiple SQL dialects (MySQL, Snowflake, BigQuery) multiplies introspection, query-generation, and testing surface area within a 2–3 week solo timeline.

**Decision:** Support PostgreSQL exclusively for v1.

**Alternatives considered:** Dialect-agnostic abstraction layer from day one (rejected: adds complexity before there's a second dialect to abstract over); SQLite for simplicity (rejected: less representative of real business database environments).

**Consequences:** Faster delivery and a cleaner story for the portfolio artifact; adding a second dialect later requires isolating the SQL-generation and introspection logic behind an interface, which is deferred, not precluded.

## ADR-002: Self-hosted deployment, not a hosted multi-tenant SaaS

**Status:** Accepted

**Context:** Accepting and storing other people's database credentials centrally creates a real security and liability program that is out of proportion to a solo portfolio project.

**Decision:** Ship as a self-hosted CLI/Docker application that the user runs against their own environment; no credentials are ever transmitted to or stored by a central service.

**Alternatives considered:** Hosted SaaS with centralized credential storage (rejected: liability and scope far exceed project timeline); browser-based tool with credentials held client-side only (rejected: adds a frontend/backend split not needed for the goal).

**Consequences:** Removes an entire category of security engineering from v1 scope (see Security Design); trades off convenience — the user must run the tool themselves.

## ADR-003: Custom plan/execute/reflect loop, not a heavy agent framework

**Status:** Accepted

**Context:** Agent frameworks (LangChain, LlamaIndex agents, etc.) provide useful abstractions but add debugging opacity and dependency weight; the loop itself is simple enough to own directly, and full control aids both the POC spike and the eval harness's ability to inspect every step.

**Decision:** Implement a minimal custom loop (see LLD) with direct function-calling against the Nebius API.

**Alternatives considered:** Adopt a full agent framework (rejected for v1: opacity and indirection when debugging the higher-risk verification behavior); use raw prompt-chaining with no explicit state object (rejected: harder to log and evaluate).

**Consequences:** More initial code to write, but a fully inspectable, log-friendly loop that the Eval Harness and the POC can reason about directly.

## ADR-004: Allow-listed operations for the sandboxed stats tool

**Status:** Accepted

**Context:** The stats tool needs to run agent-directed data operations (aggregation, trend/outlier checks) without exposing arbitrary code execution as an attack surface, even in a self-hosted personal tool.

**Decision:** Expose a fixed set of named pandas/statistics operations (e.g., groupby-aggregate, rolling mean, z-score outlier check) that the agent selects and parameterizes, rather than agent-generated free-form Python passed to `exec`/`eval`.

**Alternatives considered:** Full sandboxed subprocess/container executing arbitrary agent-written Python (rejected for v1: materially higher engineering cost for a personal tool's threat model); no stats tool at all, SQL only (rejected: limits the investigative depth that differentiates this project).

**Consequences:** Slightly less flexible than free-form code execution, but removes an entire class of code-injection risk and is faster to build and test.

## ADR-005: Verification via an independent follow-up query, not model self-critique alone

**Status:** Accepted

**Context:** Asking the same model to simply 'double check its own answer' in text is prone to confirming its own mistake (rubber-stamping). An independent, differently-phrased query against the actual data provides real evidence rather than re-stated confidence.

**Decision:** The Verifier issues a second, independently-formulated query designed to reach the same conclusion via a different path, and flags inconsistency if the two disagree.

**Alternatives considered:** Pure textual self-critique with no new query (rejected: doesn't catch data-grounded errors); a second, larger LLM as an independent judge (rejected for v1: added cost/complexity beyond the timeline; noted as a future enhancement).

**Consequences:** Meaningfully increases confidence in BR-4/BR-8 compliance at the cost of at least one extra tool call and LLM round-trip per question.

## ADR-006: Separate env var for the read-only DB role credentials

**Status:** Accepted

**Context:** The agent must connect with a SELECT-only Postgres role (BR-6), while admin/setup tasks (seeding, creating the role) still need a privileged connection string. A single `DATABASE_URL` cannot safely serve both without risking the agent using write-capable credentials.

**Decision:** Supply the agent's connection via a dedicated `READONLY_DATABASE_URL` in `.env` / `.env.example`, distinct from `DATABASE_URL`. Tests and runtime tool code that touch the target DB use the read-only URL. The `analyst_readonly` role itself is created by `scripts/create_readonly_role.sql` (also applied on demo DB init via docker-compose).

**Alternatives considered:** Overloading `DATABASE_URL` to always be the read-only role (rejected: makes local seeding and role setup awkward); deriving the read-only URL by rewriting the privileged URL in code (rejected: couples password/username conventions and hides misconfiguration).

**Consequences:** Clear separation of privileged vs agent credentials; misconfiguration fails loudly when `READONLY_DATABASE_URL` is missing. Callers must keep both variables in sync with the deployed roles.

## ADR-007: Client-side SELECT validation and LIMIT+1 truncation in `run_sql`

**Status:** Accepted

**Context:** Defense in depth (Phase 3 Security Design) requires rejecting writes before they reach Postgres, not only relying on the read-only role. Row capping must also surface whether results were cut off so the orchestrator/LLM does not treat a partial result as complete.

**Decision:** (1) Validate that each `run_sql` input is a single `SELECT`/`WITH … SELECT` in-process (comment/string-aware checks; raise `SqlValidationError` without opening a cursor for forbidden statements). (2) Open connections with `read_only=True` and an explicit `SET TRANSACTION READ ONLY`, and set `SET LOCAL statement_timeout` from `QUERY_TIMEOUT_SECONDS`. (3) When the query has no `LIMIT`, append `LIMIT ROW_LIMIT+1`, then truncate to `ROW_LIMIT` and set `truncated=True` if the extra row appeared; if a `LIMIT` is already present, still truncate client-side when more than `ROW_LIMIT` rows return.

**Alternatives considered:** Depend on `sqlparse` / a full SQL parser (rejected for v1: extra dependency for a narrow allow-list); rely solely on the DB role (rejected: writes would fail only at execution and muddy eval/error handling); silently truncate without a flag (rejected: misleads downstream reasoning).

**Consequences:** Clear pre-execution errors for DML/DDL; timeouts surface as `QueryCanceled`; callers must handle `truncated`. Exotic SQL edge cases may need validator tightening later.

## ADR-008: OpenAI tools format for Nebius function calling

**Status:** Accepted

**Context:** Nebius AI Builder exposes an OpenAI-compatible `/chat/completions` API. The minimal agent must advertise `run_sql` (and later other tools) in a form the model understands, without pulling in a heavyweight agent framework (ADR-003).

**Decision:** Use the OpenAI **tools** schema (`{"type":"function","function":{"name","description","parameters"}}`) and parse `message.tool_calls[0]` into a `ToolCall`. Implement the client with `requests` against `NEBIUS_BASE_URL`, not a Nebius-specific SDK. Log token usage from the response `usage` object on every call.

**Alternatives considered:** Legacy `functions` / `function_call` fields (rejected: older OpenAI shape; tools is the current compatible path); adopt the `openai` Python SDK (deferred: thin `requests` wrapper keeps dependencies minimal and easy to mock in CI).

**Consequences:** Tool schemas stay portable to any OpenAI-compatible endpoint; tests mock HTTP at the session layer. If Nebius drifts from the tools format, only `NebiusClient._parse_result` / payload construction need updates.

## POC Spike Results

Throwaway spike (`src/orchestrator/poc.py`, `src/tools/verifier.py`, `scripts/run_poc_spike.py`) run against the sample DB + Nebius chat completions. Ground truth from the seed: Q1 = **200** orders; Q2 MoM growth leader = **Central** (+6 in 2025-01); Q3 Apparel×store `SUM(line_total)` = **7719.44**.

| ID | Question | Unattended success? | Notes |
| --- | --- | --- | --- |
| Q1 one-step | How many orders are in the database? | **No** | First attempt: model id `meta-llama/Meta-Llama-3.1-70B-Instruct` returned 404 “model does not exist”. After switching to a catalog model, Nebius calls timed out (`Read timed out` at 60s) before a draft answer / verify. |
| Q2 multi-step | Which region had the largest month-over-month order growth? | **No** | Same Nebius timeout failure; loop + SQL path not completed unattended. |
| Q3 trap | Apparel revenue via store channel (`line_total`) | **No** | Same timeout; no draft answer produced, so verification never ran. |

**Did verification catch the trap question?** **Not evaluated** — the trap never reached `ready_to_answer` / `verify()` because the LLM HTTP layer timed out. The verifier implementation (one differently phrased SELECT + consistency compare) is in place for Week 2, but this spike did not produce an empirical pass/fail on the trap.

**Go / Redesign decision for Week 2 full build:** **Go, with hardening** — keep the custom plan/execute/ready loop and independent-SQL verify design (ADR-003 / ADR-005). Before relying on unattended multi-step runs: pin a known-good `NEBIUS_MODEL` from `/models`, raise `NEBIUS_TIMEOUT_SECONDS` for tool-calling turns, preload schema outside the LLM loop (already in the spike), and add retries/backoff on Nebius timeouts. Replace this throwaway POC in WBS-4.4; do not treat the failed live run as a redesign of the architecture.

## ADR-009: Production loop follows LLD + POC hardening (not a redesign)

**Status:** Accepted

**Context:** The POC spike failed unattended runs due to Nebius model/timeout issues, but explicitly chose **Go, with hardening** rather than Redesign. Week-2 still needs BR-7 (ask when uncertain) as a first-class plan action.

**Decision:** Implement `src/orchestrator/loop.py` per Document 3 pseudocode (`InvestigationState`, plan → execute → reflect, `MAX_ITERATIONS`), incorporating POC hardening: schema preload outside the LLM loop, retries/backoff on Nebius errors (`NEBIUS_MAX_RETRIES`), and a `needs_clarification` tool that stops the loop and returns a clarifying question instead of guessing. `ready_to_answer` maps to LLD `ANSWER_READY`; exhausting the cap yields an explicit `uncertain` status.

**Alternatives considered:** Redesign around a framework agent (rejected per ADR-003 and POC Go); treat free-text LLM prose as answers without tools (rejected: harder to eval and easier to hallucinate); continue guessing when ambiguous (rejected: violates BR-7).

**Consequences:** Clear terminal statuses for CLI/eval (`ok`, `uncertain`, `needs_clarification`, `verification_failed`). Callers must surface clarification to the user. Throwaway `poc.py` remains for historical spike runs but is not the production path.

## ADR-010: Verification must be metric-aligned (refine ADR-005)

**Status:** Accepted

**Context:** Benchmark failures BQ-02/BQ-10 showed the verifier rejecting correct drafts because the follow-up SQL measured a *different* quantity (e.g. `COUNT(DISTINCT customer_id) FROM orders` vs customer-table row count; wrong AOV definition). ADR-005 required an independent query, but not that it preserve metric/entity/grain.

**Decision:** Keep independent-SQL verification, but require metric alignment: prompts forbid entity substitution; a heuristic + LLM alignment check regenerates misaligned SQL once; the compare step must set `metric_aligned` and must not overturn an evidence-backed claim solely due to a misaligned check. Separately, narrow `needs_clarification` so defaultable “all available dates” MoM questions and SQL NULL aggregates are handled in-loop rather than asked of the user.

**Alternatives considered:** Drop verification on count questions (rejected: weakens BR-4); always trust investigation SQL over verify (rejected: removes the independent check); question-specific allow-lists (rejected: non-general).

**Consequences:** Slightly more LLM calls per verification (alignment + possible regenerate). Residual risk: alignment heuristics/LLM can still misfire on complex grains.

## ADR-011: Model-agnostic LLM provider abstraction (amends BR-10)

**Status:** Accepted

**Context:** v1 was hardwired to Nebius per BR-10 (“operate within Nebius program resources”). Users want to bring their own hosted provider (OpenAI, Anthropic, Google) without forking the orchestrator. This is a deliberate post-v1.0.0 scope change (CR-1a), not silent scope creep — BR-10 is amended explicitly here.

**Decision:** Introduce an `LLMProvider` ABC with `chat(...) -> LLMResponse`, concrete adapters (`NebiusProvider`, `OpenAIProvider`, `AnthropicProvider`, `GoogleProvider`), and `get_llm_provider()` selected via `LLM_PROVIDER` (default `nebius`). Internal tool schemas stay OpenAI-shaped (docs/API.md); each adapter translates to its native function-calling format. Only the selected provider’s API key is required.

**Alternatives considered:** Keep Nebius-only (rejected: blocks BYO-provider users); a LangChain-style universal client (rejected: same opacity/dependency concern as ADR-003).

**Consequences:** BR-10 is amended from “must use Nebius” to “must support at least one hosted provider, Nebius by default.” Each new provider is one more adapter to maintain and test. Orchestrator/verifier/formatter depend on the interface, not Nebius HTTP details.

