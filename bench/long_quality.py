#!/usr/bin/env python3
"""Matched 8k/32k long-context retrieval quality set."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_acceptance as c


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tag", default="long-quality-v1")
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--baseline-report")
    args = parser.parse_args()
    if args.cases < 2 or args.cases % 2:
        parser.error("cases must be a positive even number")

    rows = []
    positions = ("start", "middle", "end")
    for index in range(args.cases):
        context = "8k" if index % 2 == 0 else "32k"
        units = 574 if context == "8k" else 2300
        position = positions[index % len(positions)]
        prefix, codes = c.build_prefix(args.tag, index, units)
        payload = {
            "model": "qwen38-flash-next",
            "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": args.max_new_tokens,
            "return_cached_tokens_details": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": c.build_prompt(prefix, index, position)}],
        }
        row = c.parse_stream(args.url, payload, args.timeout)
        text = str(row.get("output_text") or "").strip()
        row.update({
            "id": f"{context}:{index}",
            "context": context,
            "position": position,
            "expected_code": codes[position],
            "correct": not row.get("error") and codes[position] in text,
        })
        rows.append(row)
        print(json.dumps({"completed": index + 1, "total": args.cases, "context": context, "position": position, "correct": row["correct"]}, ensure_ascii=False), flush=True)

    by_context = {}
    for context in ("8k", "32k"):
        subset = [row for row in rows if row["context"] == context]
        by_context[context] = {
            "tasks": len(subset),
            "correct": sum(bool(row["correct"]) for row in subset),
            "errors": sum(bool(row.get("error")) for row in subset),
            "wrong_or_truncated": [row["id"] for row in subset if not row["correct"]],
        }

    comparison = None
    if args.baseline_report:
        baseline = json.loads(Path(args.baseline_report).read_text(encoding="utf-8"))
        old_rows = {row["id"]: row for row in baseline["rows"]}
        changes = []
        for row in rows:
            old = old_rows.get(row["id"])
            if old is None:
                continue
            changes.append({
                "id": row["id"],
                "baseline_correct": old["correct"],
                "current_correct": row["correct"],
                "same_output": old.get("output_text", "").strip() == str(row.get("output_text") or "").strip(),
            })
        comparison = {
            "changes": changes,
            "same_outputs": sum(change["same_output"] for change in changes),
            "baseline_correct_current_wrong": sum(change["baseline_correct"] and not change["current_correct"] for change in changes),
            "baseline_wrong_current_correct": sum(not change["baseline_correct"] and change["current_correct"] for change in changes),
        }

    passed = all(row["correct"] and not row.get("error") for row in rows)
    report = {
        "args": vars(args),
        "rows": rows,
        "by_context": by_context,
        "overall": {
            "tasks": len(rows),
            "correct": sum(bool(row["correct"]) for row in rows),
            "errors": sum(bool(row.get("error")) for row in rows),
        },
        "baseline_comparison": comparison,
        "passed": passed,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "by_context": by_context, "baseline_comparison": comparison, "passed": passed}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
