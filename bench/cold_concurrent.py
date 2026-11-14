#!/usr/bin/env python3
"""Two-client cold-prefix benchmark with fixed request shapes."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_acceptance as c


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "min": None, "max": None}
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--pairs", type=int, default=10)
    parser.add_argument("--prefix-units", type=int, default=574)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if args.pairs < 1 or args.prefix_units < 10 or args.max_new_tokens < 1:
        parser.error("pairs, prefix-units and max-new-tokens must be positive")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "summary.json").exists():
        raise SystemExit("output summary already exists")

    def request(doc_id: int) -> dict[str, object]:
        prefix, codes = c.build_prefix(args.round_id, doc_id, args.prefix_units)
        payload = {
            "model": "qwen38-flash-next",
            "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": args.max_new_tokens,
            "ignore_eos": True,
            "return_cached_tokens_details": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": c.build_prompt(prefix, doc_id, "end")}],
        }
        row = c.parse_stream(args.url, payload, args.timeout)
        text = str(row.get("output_text") or "")
        row.update({
            "doc_id": doc_id,
            "expected_code": codes["end"],
            "answer_correct": not row.get("error") and codes["end"] in text,
            "output_time_s": (row["elapsed_s"] - row["ttft_s"]) if row.get("ttft_s") is not None else None,
            "single_decode_tok_s": (1.0 / row["tpot_s"]) if row.get("tpot_s") else None,
        })
        return row

    warmups = [request(9000 + index) for index in range(2)]
    pair_rows = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for pair in range(args.pairs):
            ids = [pair * 2, pair * 2 + 1]
            started = c.now()
            rows = list(executor.map(request, ids))
            pair_rows.append({"pair": pair, "wall_s": c.now() - started, "rows": rows})

    rows = [row for pair in pair_rows for row in pair["rows"]]
    good = [row for row in rows if not row.get("error")]
    total_wall = sum(pair["wall_s"] for pair in pair_rows)
    total_completion = sum(int(row.get("completion_tokens") or 0) for row in good)
    summary = {
        "requests": len(rows),
        "successful_requests": len(good),
        "all_correct": all(row.get("answer_correct") for row in rows),
        "all_expected_output_lengths": all(row.get("completion_tokens") == args.max_new_tokens for row in rows),
        "all_cold": all(not row.get("cached_tokens") for row in rows),
        "ttft_s": stats([float(row["ttft_s"]) for row in good if row.get("ttft_s") is not None]),
        "tpot_s": stats([float(row["tpot_s"]) for row in good if row.get("tpot_s") is not None]),
        "output_time_s": stats([float(row["output_time_s"]) for row in good if row.get("output_time_s") is not None]),
        "single_decode_tok_s": stats([float(row["single_decode_tok_s"]) for row in good if row.get("single_decode_tok_s") is not None]),
        "single_e2e_tok_s": stats([float(row["output_tok_per_s"]) for row in good if row.get("output_tok_per_s") is not None]),
        "instance_output_tok_s": total_completion / total_wall if total_wall else None,
        "instance_req_s": len(good) / total_wall if total_wall else None,
        "pair_wall_s": stats([float(pair["wall_s"]) for pair in pair_rows]),
        "prompt_tokens": sorted({row.get("prompt_tokens") for row in good}),
        "completion_tokens": total_completion,
        "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in good),
        "wall_s": total_wall,
    }
    passed = (
        summary["all_correct"]
        and summary["all_expected_output_lengths"]
        and summary["all_cold"]
        and len(good) == len(rows)
    )
    summary["passed"] = passed
    report = {"args": vars(args), "warmups": warmups, "pairs": pair_rows, "summary": summary}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
