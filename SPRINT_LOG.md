# Sprint Log

## Sprint 1

**Completed:** WBS-3.1 (read-only role), WBS-4.2 (SQL executor), WBS-4.7 (Nebius client + CLI), POC spike, WBS-4.4 (production loop), WBS-4.3 (stats engine), WBS-4.5 (verify), WBS-4.6 (formatter).

**POC outcome:** Go, with hardening (not Redesign). Live unattended Q1–Q3 failed on Nebius model/timeouts; trap verification not evaluated. Architecture kept; preload schema, pin model, raise timeouts, retries.

**New ADRs:** ADR-006 (READONLY_DATABASE_URL), ADR-007 (SELECT validation / LIMIT+1), ADR-008 (OpenAI tools via requests), ADR-009 (LLD loop + clarification / POC hardening).

**Carryover / known issues:** End-to-end Nebius reliability (model pin + timeouts) still needs live soak; CI workflow path/env for `READONLY_DATABASE_URL` may need cleanup; `run_stats` not yet wired into the plan tool set; eval harness / benchmark scoring deferred to Sprint 2.

**Schedule:** On track vs Phase 2 Week-1/early build plan — core tool layer and loop landed; harden live LLM path and eval next.

## Sprint 2

**Completed:** WBS-5.1–5.3 (benchmark + harness + failure-mode fixes), WBS-6.1–6.2 (README + architecture diagrams). Live eval score **10/12**.

## Sprint 3

v1.0.0 documentation/architecture closeout (README + HLD/LLD embeds).

## Sprint 4 — CR-1

**Shipped (v1.1.0 minor bump):** Post-v1.0.0 change request CR-1 — additive capability; CLI `ask` interface unchanged from v1.0.0.

- **CR-1a:** Model-agnostic `LLMProvider` (`Nebius` / `OpenAI` / `Anthropic` / `Google`) via `LLM_PROVIDER`; BR-10 amended in **ADR-011** (not a silent rewrite of Nebius-only).
- **CR-1b:** Bring-your-own-database clarity in README + `docs/DB.md` (demo compose vs real Postgres; per-DB `create_readonly_role.sql`; `READONLY_DATABASE_URL` / **ADR-006**).
- **CR-1c:** TTY-only Rich CLI banner (provider/model + DB host/name, no credentials).

**Release:** Tag `v1.1.0` — minor version: new providers/docs/UX without breaking the v1.0.0 CLI contract.
