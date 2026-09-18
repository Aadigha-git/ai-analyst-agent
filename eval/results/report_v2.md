# Evaluation Report (v2)

_Generated: 2026-09-18 18:40 UTC_

## Overall score: **pending (live run blocked)**

Attempted `python eval/eval_harness.py --benchmark eval/benchmark_v2.json` this sprint against the default/configured providers. No complete 30/30 scorecard was produced:

| Provider | Result |
| --- | --- |
| Nebius (default) | `403 Forbidden` on chat completions |
| OpenAI | `429` insufficient quota / credit balance exhausted |
| Google (`gemini-2.0-flash`) | tool schema `additionalProperties` rejected (`400`); model also reported unavailable (`404`) |
| Anthropic | API key not configured |

Re-run when a working provider key/quota is available:

```bash
LLM_PROVIDER=<provider> python eval/eval_harness.py --benchmark eval/benchmark_v2.json
# writes eval/results/report_v2.md
```

Historical **v1** baseline remains **[10/12](report.md)** on `eval/benchmark_questions.json`.

## Failure notes (live v2)

No graded v2 failures to list — the suite did not finish under a working LLM this sprint. Unit/regression coverage for glossary disclosure, smoke gate, MCP guardrails, and chart/export paths still passed offline.

## Known residual limitations (for README)

Pull these until a green `report_v2.md` score replaces them:

1. **Live v2 score not yet recorded** — full 30-question suite awaits a working default-provider key.
2. **BQ-07 (MoM growth draft completeness)** — v1 carryover: agent often names **Central** but may omit growth magnitude (+6) / month pair in the draft the rubric checks.
3. **BQ-11 disclosure quality is structural but still LLM-phrased** — v2-1/v2-2 record glossary defaults and append `Assumption:` lines; narrative wording quality still depends on the model.
4. **Google provider tool schemas** — `additionalProperties` in OpenAI-shaped tool JSON is rejected by Gemini; needs adapter scrubbing before Google is eval-ready.
5. **Cross-model comparison empty** — see `comparison_v2.md` (pending first successful `--compare-providers` run).
