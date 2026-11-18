"""The simulated eval is reproducible and its report reflects the raw records."""

from __future__ import annotations

import json


async def test_same_seed_gives_identical_records(tmp_path, monkeypatch):
    from eval import run as harness

    tasks = harness.load_tasks(3)
    records = []
    for _ in range(2):
        batch = []
        for task in tasks:
            for system in harness.SYSTEMS:
                record = await harness.run_one(task, system, "chaos", seed=1, live=False)
                record.pop("wall_ms")
                batch.append(record)
        records.append(batch)
    assert records[0] == records[1]


async def test_outage_separates_full_from_baselines():
    from eval import run as harness

    task = harness.load_tasks(1)[0]
    outcomes = {s: await harness.run_one(task, s, "outage", seed=1, live=False) for s in harness.SYSTEMS}
    assert not outcomes["naive"]["answered"] and not outcomes["retry_only"]["answered"]
    assert outcomes["full"]["answered"] and outcomes["full"]["fallbacks"] >= 1


def test_report_counts_match_records(tmp_path):
    from eval.report import write_report

    rows = [{"task_id": f"t{i}", "category": "c", "system": "full", "profile": "clean", "seed": 1,
             "mode": "simulated", "answered": i % 2 == 0, "good": False, "calls": 2, "tokens": 10,
             "fallbacks": 0, "retries": 0, "reflections": 0} for i in range(4)]
    path = tmp_path / "x.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    report = write_report(path).read_text()
    assert "| clean | full | 4 | 50.0%" in report
