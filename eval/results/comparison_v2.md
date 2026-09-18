# Cross-model evaluation comparison (v2, Nebius-hosted)

_Generated: 2026-09-18 23:14 UTC_

Manual / offline only — incurs real LLM API cost. Not run in CI (smoke subset only).

## Model scores

| Model | Score | Notes |
| --- | --- | --- |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | **20/30** |  |
| `openai/gpt-oss-120b` | **20/30** |  |
| `Qwen/Qwen3-235B-A22B-Instruct-2507` | **26/30** |  |

## Notable differences

Questions where models disagreed on pass/fail (glossary-default disclosure and trap items called out first when present):

- **BQ-10** (glossary-default / disclosure): Which customer segment has the highest average order value (mean of per-order sum of line_total)? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-12** (glossary-default / disclosure; trap): How are sales performing recently? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-14** (glossary-default / disclosure): What is total revenue across all order items? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=FAIL
- **BQ-18** (glossary-default / disclosure): Which product category has the highest total revenue? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-30** (glossary-default / disclosure): Which sales channel has the highest average order value? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-02**: How many customers are there? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-04**: How many orders were placed through the web channel? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-07**: Which region had the largest month-over-month order growth (by order count)? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=FAIL
- **BQ-09**: How many orders did Consumer-segment customers place via the web channel? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=FAIL
- **BQ-15**: What is total Electronics revenue in 2024? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-22**: What is the average quantity per order line item? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-23**: Which sales channel has the most orders? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-26**: How many orders have more than one line item? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-27**: Which customer_id placed the most orders? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=FAIL, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-28**: How many orders did North-region Enterprise customers place? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=FAIL, Qwen/Qwen3-235B-A22B-Instruct-2507=PASS
- **BQ-29**: What is the total revenue for Home products sold through the web channel? — nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B=PASS, openai/gpt-oss-120b=PASS, Qwen/Qwen3-235B-A22B-Instruct-2507=FAIL
