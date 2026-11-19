# Live eval on Amazon Bedrock, v1 (AI-graded)

Runs: 80 · grader: `qwen.qwen3-32b-v1:0` (a different model from the one that wrote the answers). Rubric coverage is the share of each task's rubric points the grader marked as addressed; score is its 1–5 overall rating. **AI-graded, not human-reviewed**: use it to compare systems, not as an absolute quality claim. Intervals are 95% bootstrap over tasks.

| system | n | answered | rubric coverage | score (1–5) | calls/task | tokens/task | cost/task (USD) | LLM p50 ms | wall s/task |
|---|---|---|---|---|---|---|---|---|---|
| naive | 40 | 100.0% | 90.0% (85.0–95.0) | 4.70 | 1.0 | 1997 | 0.00137 | 6227 | 6.2 |
| full | 40 | 87.5% | 73.3% (62.5–84.2) | 4.05 | 22.6 | 38348 | 0.01476 | 903 | 51.8 |

Total model cost of the answer runs: $0.6453 (grading calls not included).
