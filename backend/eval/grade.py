"""Grade live-eval answers against each task's rubric with an independent model.

    cd backend && python -m eval.grade eval/results/live.jsonl [--bedrock --profile <p>]

For every answer the grader (a different model from the one that wrote it) decides, item by
item, whether the rubric point is addressed, and gives a 1-5 overall score. This is
AI-graded: it is a cheap, repeatable signal, not a human judgement, and the report says so.
Writes <name>.graded.jsonl and <name>.graded.md next to the input.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRADER_SYSTEM = "You grade answers strictly against a rubric. Reply with JSON only."


def grading_prompt(task: str, rubric: list[str], answer: str) -> str:
    items = "\n".join(f"- {item}" for item in rubric)
    return (
        f"Task:\n{task}\n\nRubric points the answer must address:\n{items}\n\n"
        f"Answer to grade:\n<answer>\n{answer[:6000]}\n</answer>\n\n"
        'Reply with JSON: {"covered": {"<rubric point>": true|false, ...}, '
        '"score": <1-5 overall usefulness>, "reason": "<one sentence>"}. '
        "Mark a point true only if the answer addresses it concretely."
    )


def parse_grade(text: str, rubric: list[str]) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    covered_raw = {str(k).strip().lower(): bool(v) for k, v in (data.get("covered") or {}).items()}
    covered = {item: covered_raw.get(item.lower(), False) for item in rubric}
    try:
        score = max(1, min(5, int(data.get("score"))))
    except (TypeError, ValueError):
        return None
    return {"covered": covered, "score": score, "reason": str(data.get("reason") or "")[:300]}


async def grade_all(records: list[dict], grader_model: str) -> list[dict]:
    from services.llm_service import LLMError, call_llm

    graded = []
    tasks = {t["id"]: t for t in (json.loads(line) for line in (HERE / "tasks.jsonl").read_text().splitlines())}
    for record in records:
        rubric = record.get("rubric") or []
        result = None
        if record.get("answer"):
            try:
                response = await call_llm(prompt=grading_prompt(tasks[record["task_id"]]["task"], rubric, record["answer"]),
                                          system_prompt=GRADER_SYSTEM, model=grader_model, temperature=0.0,
                                          max_tokens=1500, json_mode=True, timeout=90)
                result = parse_grade(response.text, rubric)
            except LLMError as exc:
                result = {"error": type(exc).__name__}
        coverage = (sum(result["covered"].values()) / len(rubric)) if result and "covered" in result and rubric else 0.0
        graded.append({**record, "grade": result, "rubric_coverage": coverage,
                       "grade_score": (result or {}).get("score", 0) if result and "score" in result else 0})
    return graded


def write_report(path: Path, graded: list[dict], grader_model: str) -> Path:
    from eval.report import bootstrap_ci

    by_system: dict[str, list[dict]] = defaultdict(list)
    for record in graded:
        by_system[record["system"]].append(record)
    lines = [
        "# Live eval on Amazon Bedrock (AI-graded)",
        "",
        f"Runs: {len(graded)} · grader: `{grader_model}` (a different model from the one that wrote the answers). "
        "Rubric coverage is the share of each task's rubric points the grader marked as addressed; score is its "
        "1–5 overall rating. **AI-graded, not human-reviewed**: use it to compare systems, not as an absolute "
        "quality claim. Intervals are 95% bootstrap over tasks.",
        "",
        "| system | n | answered | rubric coverage | score (1–5) | calls/task | tokens/task | cost/task (USD) | "
        "LLM p50 ms | wall s/task |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for system in ("naive", "retry_only", "full"):
        rows = by_system.get(system)
        if not rows:
            continue
        n = len(rows)
        answered = bootstrap_ci([1.0 if r["answered"] else 0.0 for r in rows])
        coverage = bootstrap_ci([r["rubric_coverage"] for r in rows])
        score = sum(r["grade_score"] for r in rows) / n
        costs = [r.get("cost_usd") for r in rows]
        cost = (f"{sum(costs) / n:.5f}" if all(c is not None for c in costs) else "not priced")
        lines.append(
            f"| {system} | {n} | {answered[0] * 100:.1f}% | {coverage[0] * 100:.1f}% "
            f"({coverage[1] * 100:.1f}–{coverage[2] * 100:.1f}) | {score:.2f} | "
            f"{sum(r['calls'] for r in rows) / n:.1f} | {sum(r['tokens'] for r in rows) / n:.0f} | {cost} | "
            f"{sum(r.get('llm_latency_p50_ms', 0) for r in rows) / n:.0f} | {sum(r['wall_ms'] for r in rows) / n / 1000:.1f} |"
        )
    total_cost = [r.get("cost_usd") for r in graded]
    if all(c is not None for c in total_cost):
        lines += ["", f"Total model cost of the answer runs: ${sum(total_cost):.4f} (grading calls not included)."]
    out = path.with_suffix(".graded.md")
    out.write_text("\n".join(lines) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--grader", default=None, help="grader model id (default: the Bedrock fallback model)")
    parser.add_argument("--bedrock", action="store_true")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    sys.path.insert(0, str(HERE.parent))
    if args.bedrock:
        from eval.bedrock import DEFAULT_MODELS, configure_bedrock_env

        configure_bedrock_env(args.region, args.profile)
        args.grader = args.grader or DEFAULT_MODELS["fallback"]
    grader = args.grader or os.environ.get("FALLBACK_MODEL", "")
    records = [json.loads(line) for line in args.path.read_text().splitlines() if line.strip()]
    graded = asyncio.run(grade_all(records, grader))
    out_jsonl = args.path.with_suffix(".graded.jsonl")
    out_jsonl.write_text("\n".join(json.dumps(r) for r in graded) + "\n")
    print(f"wrote {out_jsonl} and {write_report(args.path, graded, grader)}")


if __name__ == "__main__":
    main()
