#!/usr/bin/env python3
"""Report per-target and aggregate cache coverage from cache_acceptance."""
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("report")
parser.add_argument("output")
args = parser.parse_args()
report = json.loads(Path(args.report).read_text(encoding="utf-8"))
rows = []
for row in report["rows"]:
    if row["phase"] != "target_replay":
        continue
    details = row.get("cached_tokens_details") or {}
    prompt = int(row["prompt_tokens"])
    cached = int(row.get("cached_tokens") or 0)
    rows.append({
        "doc_id": row["doc_id"],
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "device_tokens": int(details.get("device") or 0),
        "host_tokens": int(details.get("host") or 0),
        "storage_tokens": int(details.get("storage") or 0),
        "full_coverage": cached / prompt if prompt else 0.0,
        "storage_coverage": float(row.get("storage_coverage") or 0.0),
        "exact_answer": bool(row.get("exact_answer")),
    })
rows.sort(key=lambda row: row["doc_id"])
totals = {
    "doc_id": "OVERALL",
    "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
    "cached_tokens": sum(row["cached_tokens"] for row in rows),
    "device_tokens": sum(row["device_tokens"] for row in rows),
    "host_tokens": sum(row["host_tokens"] for row in rows),
    "storage_tokens": sum(row["storage_tokens"] for row in rows),
    "full_coverage": sum(row["cached_tokens"] for row in rows) / sum(row["prompt_tokens"] for row in rows) if rows else 0.0,
    "storage_coverage": sum(row["storage_tokens"] for row in rows) / sum(row["prompt_tokens"] for row in rows) if rows else 0.0,
    "exact_answer": all(row["exact_answer"] for row in rows),
}
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
(output / "coverage.json").write_text(json.dumps({"targets": rows, "overall": totals}, ensure_ascii=False, indent=2), encoding="utf-8")
with (output / "coverage.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else list(totals))
    writer.writeheader()
    writer.writerows(rows + [totals])
print(json.dumps({"targets": rows, "overall": totals}, ensure_ascii=False, indent=2))
