"""Turn eval JSONL into a Markdown report with bootstrap confidence intervals."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

BOOTSTRAP_SAMPLES = 2_000


def bootstrap_ci(values: list[float], seed: int = 7) -> tuple[float, float, float]:
    """Mean and 95% percentile-bootstrap interval (resampling tasks)."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(BOOTSTRAP_SAMPLES))
    return sum(values) / n, means[int(0.025 * BOOTSTRAP_SAMPLES)], means[int(0.975 * BOOTSTRAP_SAMPLES) - 1]


def _pct(triple: tuple[float, float, float]) -> str:
    mean, low, high = triple
    return f"{mean * 100:.1f}% ({low * 100:.1f}–{high * 100:.1f})"


def summarise(records: list[dict]) -> dict[tuple[str, str], dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        groups[(record["profile"], record["system"])].append(record)
    table = {}
    for key, rows in groups.items():
        n = len(rows)
        table[key] = {
            "n": n,
            "answered": bootstrap_ci([1.0 if r["answered"] else 0.0 for r in rows]),
            "good": bootstrap_ci([1.0 if r["good"] else 0.0 for r in rows]),
            "calls": sum(r["calls"] for r in rows) / n,
            "tokens": sum(r["tokens"] for r in rows) / n,
            "fallbacks": sum(r["fallbacks"] for r in rows) / n,
            "retries": sum(r["retries"] for r in rows) / n,
            "reflections": sum(r["reflections"] for r in rows) / n,
        }
    return table


def write_report(jsonl_path: Path) -> Path:
    records = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    table = summarise(records)
    mode = records[0]["mode"] if records else "unknown"
    seeds = sorted({r["seed"] for r in records})
    tasks = sorted({r["task_id"] for r in records})
    lines = [
        f"# Orchestration eval ({mode})",
        "",
        f"Runs: {len(records)} · tasks: {len(tasks)} · seeds: {seeds} · generated from `{jsonl_path.name}` by "
        "`eval/report.py`.",
        "",
    ]
    if mode == "simulated":
        lines += [
            "**Read this first.** These runs use `eval/simulator.py`, not a real model. They measure whether the "
            "orchestration (retries, model fallback, reflection, scheduling) recovers from the failures the "
            "simulator injects. They say nothing about answer quality with real models. The simulated judge is "
            "perfect and the failure rates are chosen by the simulator, so absolute numbers are properties of the "
            "simulator; compare systems against each other, not against the outside world.",
            "",
            "* **answered**: the run produced a final answer (naive: the single call succeeded).",
            "* **good**: every delivered part is correct by the simulator's ground truth (no low-quality marker, "
            "not truncated by chaos corruption). Naive has no validation, so its bad answers are delivered as-is.",
            "* Intervals are 95% bootstrap over tasks. Calls, tokens, fallbacks, retries and reflect visits are means "
            "per task (tokens are length/4 estimates). A reflect visit with reflection disabled is a skip.",
            "",
        ]
    lines += ["| profile | system | n | answered | good | calls | tokens | fallbacks | retries | reflect visits |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    order = {"naive": 0, "retry_only": 1, "full": 2}
    for (profile, system), row in sorted(table.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 9))):
        lines.append(
            f"| {profile} | {system} | {row['n']} | {_pct(row['answered'])} | {_pct(row['good'])} | "
            f"{row['calls']:.1f} | {row['tokens']:.0f} | {row['fallbacks']:.2f} | {row['retries']:.2f} | "
            f"{row['reflections']:.2f} |"
        )
    lines.append("")
    out = jsonl_path.with_suffix(".md")
    out.write_text("\n".join(lines))
    return out
