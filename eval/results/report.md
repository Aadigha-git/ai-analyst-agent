# Evaluation Report

_Generated: 2026-09-13 23:44 UTC_

## Overall score: **6/12**

| ID | Pass | Multi-step | Trap | Agent status | Failure category |
| --- | --- | --- | --- | --- | --- |
| BQ-01 | PASS | no | no | ok | — |
| BQ-02 | FAIL | no | no | verification_failed | verification |
| BQ-03 | FAIL | no | no | ok | factual |
| BQ-04 | PASS | no | no | ok | — |
| BQ-05 | PASS | no | no | ok | — |
| BQ-06 | PASS | no | no | ok | — |
| BQ-07 | FAIL | yes | no | needs_clarification | trap_handling |
| BQ-08 | FAIL | yes | no | needs_clarification | multi_step |
| BQ-09 | PASS | yes | no | ok | — |
| BQ-10 | FAIL | yes | no | verification_failed | verification |
| BQ-11 | FAIL | yes | yes | ok | trap_handling |
| BQ-12 | PASS | no | yes | needs_clarification | — |

## Failure notes

### BQ-02 — `verification`

- **Question:** How many customers are there?
- **Expected:** 80
- **Agent (verification_failed):** [verification_failed] draft=There are 80 customers.; detail=The claimed 80 customers does not match the verified count of 73 distinct customer_ids
- **Grader:** The agent's answer did not match the verified count, resulting in a verification failure
- **Note:** Failure attributed to verification (draft inconsistent with independent check).

### BQ-03 — `factual`

- **Question:** How many products are in the catalog?
- **Expected:** 40
- **Agent (ok):** There are 40 products in the catalog.
- **Grader:** Agent answer matches expected answer, but does not explicitly mention using the products table.
- **Note:** Failure category `factual`.

### BQ-07 — `trap_handling`

- **Question:** Which region had the largest month-over-month order growth (by order count)?
- **Expected:** Central region, +6 orders from Dec 2024 to Jan 2025 (largest MoM growth)
- **Agent (needs_clarification):** [needs_clarification] What time range are we considering for the month-over-month order growth?
Reason: The question is ambiguous because it does not specify a time range for the month-over-month order growth.
- **Grader:** Agent incorrectly requested clarification instead of providing an answer based on the most relevant time range.
- **Note:** Failure attributed to trap-question handling (should clarify or state assumptions; guessed instead).

### BQ-08 — `multi_step`

- **Question:** What is the total revenue (sum of line_total) for Apparel products sold through the store channel?
- **Expected:** 7719.44
- **Agent (needs_clarification):** [needs_clarification] What would you like to do with the null result?
Reason: The query returned a null result, so it is unclear how to proceed.
- **Grader:** The agent did not provide a valid answer and instead requested clarification, indicating a lack of understanding of the multi-step query required to solve the problem.
- **Note:** Failure attributed to multi-step reasoning (join / second-query path).

### BQ-10 — `verification`

- **Question:** Which customer segment has the highest average order value (mean of per-order sum of line_total)?
- **Expected:** Consumer, with AOV 1736.36
- **Agent (verification_failed):** [verification_failed] draft=The customer segment with the highest average order value is Consumer.; detail=The verification result shows the segment with the highest average order value is Enterprise, not Consumer.
- **Grader:** Agent answer failed verification, incorrectly identifying Enterprise as the segment with the highest average order value instead of Consumer
- **Note:** Failure attributed to verification (draft inconsistent with independent check).

### BQ-11 — `trap_handling`

- **Question:** What is total Electronics revenue?
- **Expected:** Ambiguous without a time range; if all-time sum(line_total) for Electronics is intended, the value is 157640.18. Prefer asking whether to use a date window (e.g. 2024 alone is 63428.38).
- **Agent (ok):** The total Electronics revenue is $157640.18.
- **Grader:** The agent failed to recognize the ambiguity in the question and did not ask for clarification or explicitly state its assumptions, leading to a potentially misleading answer.
- **Note:** Failure attributed to trap-question handling (should clarify or state assumptions; guessed instead).

