#!/usr/bin/env python3
"""Generate the logtriage demo task set (deterministic).

Builds 10 synthetic access logs, computes ground-truth stats with the demo
skill's own ``scripts/parse_logs.py`` (so rubric numbers can never drift from
the parser), and writes ``logtriage_tasks/{train,val,test}/items.json``.

Run from the repo root:  python3 data/skilleval_demo/make_logtriage_tasks.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "logtriage_skill")
OUT_DIR = os.path.join(HERE, "logtriage_tasks")

ENDPOINTS = ["/api/users", "/api/orders", "/api/search", "/api/items",
             "/checkout", "/login", "/health"]
METHODS = ["GET", "GET", "GET", "POST", "POST", "PUT"]
OK_STATUSES = [200, 200, 200, 200, 201, 204, 301, 404]
ERR_STATUSES = [500, 502, 503]


def _load_parser():
    spec = importlib.util.spec_from_file_location(
        "parse_logs", os.path.join(SKILL_DIR, "scripts", "parse_logs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse


def _make_log(rng: random.Random, n_lines: int, err_frac: float,
              n_malformed: int) -> str:
    lines = []
    for i in range(n_lines):
        ts = f"2026-06-{rng.randint(10, 28):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
        method = rng.choice(METHODS)
        path = rng.choice(ENDPOINTS)
        is_err = rng.random() < err_frac
        status = rng.choice(ERR_STATUSES if is_err else OK_STATUSES)
        latency = rng.randint(600, 950) if is_err else rng.randint(18, 420)
        lines.append(f"{ts}|{method}|{path}|{status}|{latency}ms")
    garbage = ["OOM killer invoked on worker-3",
               "2026-06-14T09:12:33Z|GET|/api/users|garbled",
               "restarting..."]
    for i in range(n_malformed):
        lines.insert(rng.randint(0, len(lines)), garbage[i % len(garbage)])
    return "\n".join(lines) + "\n"


def _rubric(log_path: str, stats: dict) -> str:
    verdict = "OK" if stats["error_rate_pct"] < 5.0 else "ATTENTION"
    numbers = (f"total_requests={stats['total_requests']}, "
               f"error_count={stats['error_count']}, "
               f"error_rate={stats['error_rate_pct']}% (one decimal), "
               f"p95_latency={stats['p95_latency_ms']}ms, "
               f"top_endpoint={stats['top_endpoint']}")
    if stats["skipped_malformed_lines"]:
        numbers += (f", and mentions that {stats['skipped_malformed_lines']} "
                    f"malformed line(s) were skipped")
    return (
        "Score = 1.0 minus 0.2 for each missing criterion (floor 0.0). "
        "Pass only if all five hold:\n"
        "1. The workspace contains a report file at exactly `triage/report.md`.\n"
        f"2. The report's first line is exactly `# Triage — {log_path}` "
        "(H1 title of the form `# Triage — <log path>`).\n"
        "3. The report contains the parser's version stamp on its own line: "
        "`tool: parse_logs v1.2`.\n"
        f"4. The report states the correct statistics: {numbers}.\n"
        "5. The report has a `## Verdict` section whose last line is exactly "
        f"`STATUS: {verdict}` (team rule: OK when error_rate < 5.0%, "
        "otherwise ATTENTION)."
    )


# (id, split, log_path, n_lines, err_frac, n_malformed)
SPECS = [
    ("logtriage_01", "train", "logs/access.log", 46, 0.00, 0),
    ("logtriage_02", "train", "logs/access.log", 62, 0.14, 2),
    ("logtriage_03", "train", "logs/api.log",    38, 0.02, 0),
    ("logtriage_04", "train", "logs/access.log", 74, 0.10, 1),
    ("logtriage_05", "val",   "logs/access.log", 51, 0.02, 0),
    ("logtriage_06", "val",   "logs/edge.log",   67, 0.12, 3),
    ("logtriage_07", "val",   "logs/access.log", 43, 0.00, 0),
    ("logtriage_08", "test",  "logs/access.log", 57, 0.03, 0),
    ("logtriage_09", "test",  "logs/api.log",    69, 0.15, 2),
    ("logtriage_10", "test",  "logs/access.log", 48, 0.11, 0),
]


def main() -> None:
    parse = _load_parser()
    rng = random.Random(20260703)
    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}

    for task_id, split, log_path, n_lines, err_frac, n_malformed in SPECS:
        log = _make_log(rng, n_lines, err_frac, n_malformed)
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as tmp:
            tmp.write(log)
            tmp_path = tmp.name
        try:
            stats = parse(tmp_path)
        finally:
            os.unlink(tmp_path)
        verdict = "OK" if stats["error_rate_pct"] < 5.0 else "ATTENTION"
        splits[split].append({
            "id": task_id,
            "question": (f"Analyze the access log at `{log_path}` and write a "
                         "triage report for the on-call engineer."),
            "rubric": _rubric(log_path, stats),
            "files": {log_path: log},
            "task_type": "routine" if verdict == "OK" else "incident",
        })
        print(f"{task_id} [{split:5s}] {verdict:9s} "
              f"n={stats['total_requests']} err={stats['error_rate_pct']}% "
              f"p95={stats['p95_latency_ms']}ms malformed={stats['skipped_malformed_lines']}")

    for split, items in splits.items():
        split_dir = os.path.join(OUT_DIR, split)
        os.makedirs(split_dir, exist_ok=True)
        with open(os.path.join(split_dir, "items.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"wrote {split}/items.json ({len(items)} tasks)")


if __name__ == "__main__":
    main()
