#!/usr/bin/env python3
"""Aggregate independent cold_concurrent rounds without pooling throughput."""
import argparse
import csv
import json
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--runs", nargs="+", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()


def stats(values):
    values = [float(v) for v in values if v is not None]
    return {"median": statistics.median(values) if values else None, "min": min(values) if values else None, "max": max(values) if values else None}

rounds = []
all_rows = []
for run in args.runs:
    root = Path(run)
    data = root / "results"
    cold = json.loads((data / "cold_concurrent/summary.json").read_text(encoding="utf-8"))
    quality = json.loads((data / "long_quality/report.json").read_text(encoding="utf-8"))
    info = json.loads((data / "server_info.json").read_text(encoding="utf-8"))
    resources = [r for r in csv.DictReader((data / "resources.csv").open()) if r["mem_available_kib"]]
    mem = [int(r["mem_available_kib"]) for r in resources]
    swap = [int(r["swap_free_kib"]) for r in resources]
    rows = [row for pair in cold["pairs"] for row in pair["rows"]]
    all_rows.extend(rows)
    rounds.append({
        "run": root.name,
        "kv_dtype": info["kv_cache_dtype"],
        "pool_tokens": info["max_total_num_tokens"],
        "kvcache": info["internal_states"][0]["memory_usage"]["kvcache"],
        "summary": cold["summary"],
        "quality_overall": quality["overall"],
        "quality_same_outputs": (quality.get("baseline_comparison") or {}).get("same_outputs"),
        "min_mem_available_gib": min(mem) / 1048576,
        "max_swap_growth_mib": (swap[0] - min(swap)) / 1024,
    })

summary = {
    "rounds": rounds,
    "requests": len(all_rows),
    "all_correct": all(row["answer_correct"] for row in all_rows),
    "all_cold": all(not row.get("cached_tokens") for row in all_rows),
    "ttft_s": stats([row["ttft_s"] for row in all_rows]),
    "tpot_s": stats([row["tpot_s"] for row in all_rows]),
    "output_time_s": stats([row["output_time_s"] for row in all_rows]),
    "single_decode_tok_s": stats([row["single_decode_tok_s"] for row in all_rows]),
    "single_e2e_tok_s": stats([row["output_tok_per_s"] for row in all_rows]),
    "instance_output_tok_s": stats([r["summary"]["instance_output_tok_s"] for r in rounds]),
    "instance_req_s": stats([r["summary"]["instance_req_s"] for r in rounds]),
    "min_mem_available_gib": stats([r["min_mem_available_gib"] for r in rounds]),
    "max_swap_growth_mib": stats([r["max_swap_growth_mib"] for r in rounds]),
    "quality_correct": sum(r["quality_overall"]["correct"] for r in rounds),
    "quality_tasks": sum(r["quality_overall"]["tasks"] for r in rounds),
}
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
(output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
