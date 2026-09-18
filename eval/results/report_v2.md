# Evaluation Report

_Generated: 2026-09-18 22:54 UTC_

## Overall score: **28/30**

| ID | Pass | Multi-step | Trap | Agent status | Failure category |
| --- | --- | --- | --- | --- | --- |
| BQ-01 | PASS | no | no | ok | — |
| BQ-02 | PASS | no | no | ok | — |
| BQ-03 | PASS | no | no | ok | — |
| BQ-04 | PASS | no | no | ok | — |
| BQ-05 | PASS | no | no | ok | — |
| BQ-06 | PASS | no | no | ok | — |
| BQ-07 | PASS | yes | no | ok | — |
| BQ-08 | PASS | yes | no | ok | — |
| BQ-09 | FAIL | yes | no | uncertain | multi_step |
| BQ-10 | PASS | yes | no | ok | — |
| BQ-11 | PASS | yes | yes | ok | — |
| BQ-12 | PASS | no | yes | needs_clarification | — |
| BQ-13 | PASS | no | no | ok | — |
| BQ-14 | PASS | no | no | ok | — |
| BQ-15 | PASS | yes | no | ok | — |
| BQ-16 | PASS | yes | no | ok | — |
| BQ-17 | PASS | yes | no | ok | — |
| BQ-18 | FAIL | yes | no | verification_failed | verification |
| BQ-19 | PASS | no | no | ok | — |
| BQ-20 | PASS | yes | no | ok | — |
| BQ-21 | PASS | no | no | ok | — |
| BQ-22 | PASS | no | no | ok | — |
| BQ-23 | PASS | no | no | ok | — |
| BQ-24 | PASS | yes | no | ok | — |
| BQ-25 | PASS | no | no | ok | — |
| BQ-26 | PASS | yes | no | ok | — |
| BQ-27 | PASS | yes | no | ok | — |
| BQ-28 | PASS | yes | no | ok | — |
| BQ-29 | PASS | yes | no | ok | — |
| BQ-30 | PASS | yes | no | ok | — |

## Failure notes

### BQ-09 — `multi_step`

- **Question:** How many orders did Consumer-segment customers place via the web channel?
- **Expected:** 20
- **Agent (uncertain):** [uncertain] Could not reach a confident answer within the iteration cap.
- **Grader:** The agent failed to produce the correct answer, did not perform the required join between orders and customers tables, and did not apply the necessary filters for segment='Consumer' and channel='web'. The failure is categorized as multi_step because the solution requires joining tables and applying multiple conditions, none of which were executed.
- **Note:** Failure attributed to multi-step reasoning (join / second-query path).

### BQ-18 — `verification`

- **Question:** Which product category has the highest total revenue?
- **Expected:** Electronics, with revenue 157640.18
- **Agent (verification_failed):** [verification_failed] draft=The product category with the highest total revenue is Electronics, generating $157,640.18 in revenue.; detail=Verification query failed: syntax error at or near "LIMIT"
LINE 2: LIMIT 501
        ^
- **Grader:** The agent correctly identified the top category and revenue but failed verification due to a syntax error and did not fulfill the rubric requirement to rank Electronics above Sports and Home. The verification failure and missing comparative ranking cause the response to not fully meet the criteria.
- **Note:** Failure attributed to verification (draft inconsistent with independent check).

