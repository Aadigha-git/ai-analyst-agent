# Cross-model evaluation comparison (v2)

_Generated: 2026-09-18 21:30 UTC_

Manual / offline only — incurs real LLM API cost. Not run in CI (smoke subset only).

## Provider scores

| Provider | Score | Model |
| --- | --- | --- |
| nebius | _skipped_ | `403 Forbidden` this sprint |
| openai | _skipped_ | no credits remaining (`429`) |
| anthropic | _skipped_ | `ANTHROPIC_API_KEY` not set |
| google | _skipped_ | `gemini-2.0-flash` unavailable (`404`); prior tool-schema `400` |

## Notable differences

Fewer than two providers produced scores, so pass/fail disagreements cannot be compared. No highest-scoring provider to report until a live `--compare-providers` run completes.

Re-run:

```bash
python eval/eval_harness.py --compare-providers
# writes eval/results/comparison_v2.md
```
