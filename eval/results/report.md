# Evaluation Report

_Generated: 2026-09-14 02:47 UTC_

## Overall score: **10/12**

| ID | Pass | Multi-step | Trap | Agent status | Failure category |
| --- | --- | --- | --- | --- | --- |
| BQ-01 | PASS | no | no | ok | — |
| BQ-02 | PASS | no | no | ok | — |
| BQ-03 | PASS | no | no | ok | — |
| BQ-04 | PASS | no | no | ok | — |
| BQ-05 | PASS | no | no | ok | — |
| BQ-06 | PASS | no | no | ok | — |
| BQ-07 | FAIL | yes | no | ok | multi_step |
| BQ-08 | PASS | yes | no | ok | — |
| BQ-09 | PASS | yes | no | ok | — |
| BQ-10 | PASS | yes | no | ok | — |
| BQ-11 | FAIL | yes | yes | ok | trap_handling |
| BQ-12 | PASS | no | yes | needs_clarification | — |

## Failure notes

### BQ-07 — `multi_step`

- **Question:** Which region had the largest month-over-month order growth (by order count)?
- **Expected:** Central region, +6 orders from Dec 2024 to Jan 2025 (largest MoM growth)
- **Agent (ok):** The region with the largest month-over-month order growth (by order count) is Central.
- **Grader:** The agent correctly identifies the region but fails to provide the necessary steps and calculations to support the answer.
- **Note:** Failure attributed to multi-step reasoning (join / second-query path).

### BQ-11 — `trap_handling`

- **Question:** What is total Electronics revenue?
- **Expected:** Ambiguous without a time range; if all-time sum(line_total) for Electronics is intended, the value is 157640.18. Prefer asking whether to use a date window (e.g. 2024 alone is 63428.38).
- **Agent (ok):** The total Electronics revenue is $157,640.18.
- **Grader:** The agent failed to recognize the ambiguity in the question regarding the time range for the total Electronics revenue and provided a numeric answer without clarification or stating an all-time assumption.
- **Note:** Failure attributed to trap-question handling (should clarify or state assumptions; guessed instead).

## Known Limitations

**BQ-07 (multi-step MoM growth):** The agent correctly named **Central** but omitted the growth magnitude (+6) and month pair in `ready_to_answer`. This is a remaining loop/output gap: drafts are not yet required to include the supporting scalar(s) that the rubric checks, and the grader (correctly) fails incomplete narratives even when the ranked entity is right. A fuller fix would enforce “answer + key figures” in the ready-to-answer contract / formatter, not a MoM-specific hack.

**BQ-11 (trap — Electronics revenue without time window):** The agent returned the correct all-time total ($157,640.18) without clarifying or stating the all-dates assumption. Clarification policy was tightened for undefined metrics and NULL handling, but open-ended “total revenue” questions still often get a silent all-time default. Fully fixing this without over-clarifying well-scoped totals remains an open BR-7 tension; the harness still expects clarification *or* an explicit assumption on this trap.

