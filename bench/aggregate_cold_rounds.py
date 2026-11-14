#!/usr/bin/env python3
"""Aggregate independent cold-prefix rounds without pooling throughput."""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--runs", nargs="+", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()


def median(values):
    values = [float(value) for value in values if value is not None]
    return statistics.median(values) if values else None


rounds = []
for run in args.runs:
    data = Path(run) / "results"
    cold = json.loads((data / "cold_concurrent/summary.json").read_text(encoding="utf-8"))
    info_path = data / "server_info.json"
    if info_path.exists():
        max_total = json.loads(info_path.read_text(encoding="utf-8"))["max_total_num_tokens"]
    else:
        log = (Path(run) / "logs/launch.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"KV Cache is allocated.*#tokens: (\d+)", log)
        max_total = int(match.group(1)) if match else None
    rows = [row for pair in cold["pairs"] for row in pair["rows"]]
    resources = [row for row in csv.DictReader((data / "resources.csv").open()) if row["mem_available_kib"]]
    mem = [int(row["mem_available_kib"]) for row in resources]
    swap_free = [int(row["swap_free_kib"]) for row in resources]
    summary = cold["summary"]
    rounds.append({
        "run": Path(run).name,
        "max_total_num_tokens": max_total,
        "requests": summary["requests"],
        "all_correct": summary["all_correct"],
        "all_cold": summary["all_cold"],
        "ttft_p50_s": summary["ttft_s"]["median"],
        "tpot_p50_s": summary["tpot_s"]["median"],
        "single_decode_tok_s_p50": summary["single_decode_tok_s"]["median"],
        "single_e2e_tok_s_p50": summary["single_e2e_tok_s"]["median"],
        "instance_output_tok_s": summary["instance_output_tok_s"],
        "instance_req_s": summary["instance_req_s"],
        "pair_wall_p50_s": summary["pair_wall_s"]["median"],
        "min_mem_available_gib": min(mem) / (1024 * 1024) if mem else None,
        "max_swap_growth_mib": (max(swap_free) - min(swap_free)) / 1024 if swap_free else None,
        "completion_tokens": sum(int(row["completion_tokens"]) for row in rows),
        "cached_tokens": sum(int(row["cached_tokens"]) for row in rows),
    })

keys = (
    "max_total_num_tokens",
    "ttft_p50_s",
    "tpot_p50_s",
    "single_decode_tok_s_p50",
    "single_e2e_tok_s_p50",
    "instance_output_tok_s",
    "instance_req_s",
    "pair_wall_p50_s",
    "min_mem_available_gib",
    "max_swap_growth_mib",
)
aggregate = {
    key: {
        "median": median(round_[key] for round_ in rounds),
        "min": min(round_[key] for round_ in rounds),
        "max": max(round_[key] for round_ in rounds),
    }
    for key in keys
}
report = {"rounds": rounds, "aggregate": aggregate}
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))