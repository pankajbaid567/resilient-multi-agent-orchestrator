# Live eval on Amazon Bedrock, v2 (AI-graded)

Runs: 80 · grader: `qwen.qwen3-32b-v1:0` (a different model from the one that wrote the answers). Rubric coverage is the share of each task's rubric points the grader marked as addressed; score is its 1–5 overall rating. **AI-graded, not human-reviewed**: use it to compare systems, not as an absolute quality claim. Intervals are 95% bootstrap over tasks.

| system | n | answered | rubric coverage | score (1–5) | calls/task | tokens/task | cost/task (USD) | LLM p50 ms | wall s/task |
|---|---|---|---|---|---|---|---|---|---|
| naive | 40 | 100.0% | 90.8% (85.8–95.8) | 4.72 | 1.0 | 1997 | 0.00137 | 6227 | 6.2 |
| full | 40 | 100.0% | 94.2% (90.0–97.5) | 4.80 | 6.9 | 10875 | 0.00424 | 749 | 12.6 |

Total model cost of the answer runs: $0.2244 (grading calls not included).
