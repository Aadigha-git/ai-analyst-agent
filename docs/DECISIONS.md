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
