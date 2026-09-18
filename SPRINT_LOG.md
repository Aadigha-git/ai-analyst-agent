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

## v2.0 Sprint 1

**Completed:** v2-1 (glossary loader / BR-11), v2-2 (assumption disclosure / BR-12), v2-3 (versioned benchmark / BR-13), v2-4 (CI smoke gate / BR-13). ADRs: **ADR-007 (v2.0)** glossary; **ADR-008 (v2.0)** smoke regression.

**BQ-11 gap:** Closed — glossary defaults are recorded on `defaults_used` and surfaced as `Assumption:` lines; regression in `tests/test_orchestrator_assumptions.py` asserts disclosure on the Electronics-revenue trap (not a silent all-time total).

**Benchmark:** `eval/benchmark_v2.json` — **30** questions (12 v1 + 18 new); `eval/benchmark_questions.json` kept as the v1.0 baseline.

**CI smoke:** Live on PRs via `eval-smoke` (`eval/run_smoke.py` vs `smoke_baseline.json` 5/5). Requires repository secret `NEBIUS_API_KEY` (or the matching default-provider key). Full 30-question suite remains manual/nightly.

## v2.0 Sprint 2

**Completed:** v2-5 (structured redacted JSONL tracing / BR-15, BR-17), v2-6 (`cli replay` / BR-16), v2-7 (`--compare-providers` / BR-14), v2-8 (chart recommendation + CSV/XLSX export / BR-19).

**Tracing / replay:** Each `ask` writes `logs/run_<timestamp>.jsonl` (row values redacted by default; `--no-redact` local-only). `python -m src.cli replay <trace.jsonl>` pretty-prints steps plus session latency/token/cost totals.

**Cross-model comparison:** Harness writes `eval/results/comparison_v2.md` (provider×score + pass/fail disagreements, glossary/trap called out). **Live headline:** no full multi-provider run landed this sprint (provider keys incomplete / Nebius 403 locally) — highest scorer and disagreements TBD on the first successful `python eval/eval_harness.py --compare-providers` nightly. CI still runs only the 5-question smoke subset.

**Export / visualization:** Rule-based `recommend_chart` prints `Suggested visualization: …` (line/bar/scatter/table only); `--export {csv,xlsx}` writes `outputs/export_<timestamp>.*`.
