#!/usr/bin/env python3
"""Concurrent hot-prefix serving benchmark."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from . import cache_acceptance as c
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cache_acceptance as c


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:31000/v1/chat/completions")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tag", default="hot-prefix")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--prefix-units", type=int, default=400)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--ttft-slo", type=float, default=5.0)
    parser.add_argument("--tpot-slo", type=float, default=0.1)
    args = parser.parse_args()
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("--repeats and --concurrency must be positive")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []
    for repeat in range(1, args.repeats + 1):
        tag = f"{args.tag}-r{repeat}"
        prefix, codes = c.build_prefix(tag, 0, args.prefix_units)
        expected = codes["end"]
        base = {
            "model": "qwen38-flash-next",
            "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": args.max_new_tokens,
            "return_cached_tokens_details": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        warm_payload = dict(base)
        warm_payload["messages"] = [{"role": "user", "content": c.build_prompt(prefix, 0, "end")}]
        warmup = c.parse_stream(args.url, warm_payload, args.timeout)

        def request(_: int) -> dict[str, object]:
            payload = dict(base)
            payload["messages"] = [{"role": "user", "content": c.build_prompt(prefix, 0, "end")}]
            row = c.parse_stream(args.url, payload, args.timeout)
            output_text = str(row.get("output_text") or "").strip()
            row.update({
                "expected_code": expected,
                "answer_correct": not row.get("error") and expected in output_text,
            })
            return row

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            rows = list(executor.map(request, range(args.concurrency)))
        wall = time.perf_counter() - start
        good = [row for row in rows if not row.get("error")]
        slo = [
            row for row in good
            if float(row.get("ttft_s") or 0.0) <= args.ttft_slo
            and (row.get("tpot_s") is None or float(row["tpot_s"]) <= args.tpot_slo)
        ]
        total_tokens = sum(int(row.get("completion_tokens") or 0) for row in good)
        tpot_values = [float(row["tpot_s"]) for row in good if row.get("tpot_s") is not None]
        report = {
            "repeat": repeat,
            "tag": tag,
            "url": args.url,
            "concurrency": args.concurrency,
            "warmup": warmup,
            "rows": rows,
            "all_correct": bool(rows) and all(bool(row.get("answer_correct")) for row in rows),
            "wall_s": wall,
            "requests": len(rows),
            "successful": len(good),
            "ttft_mean_s": statistics.mean(float(row["ttft_s"]) for row in good) if good else None,
            "ttft_p95_s": sorted(float(row["ttft_s"]) for row in good)[-1] if good else None,
            "tpot_mean_s": statistics.mean(tpot_values) if tpot_values else None,
            "wallclock_req_s": len(good) / wall if wall else 0.0,
            "wallclock_output_tok_s": total_tokens / wall if wall else 0.0,
            "slo_threshold": {"ttft_s": args.ttft_slo, "tpot_s": args.tpot_slo},
            "slo_goodput_req_s": len(slo) / wall if wall else 0.0,
            "slo_goodput_requests": len(slo),
            "cache_tier_tokens": {
                tier: sum(int((row.get("cached_tokens_details") or {}).get(tier, 0)) for row in good)
                for tier in ("device", "host", "storage")
            },
            "created_at": time.time(),
        }
        (output / f"repeat{repeat}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
        print(json.dumps({key: report[key] for key in (
            "repeat", "all_correct", "wall_s", "ttft_mean_s", "ttft_p95_s",
            "tpot_mean_s", "wallclock_req_s", "wallclock_output_tok_s",
            "slo_goodput_req_s", "cache_tier_tokens",
        )}, ensure_ascii=False), flush=True)

    (output / "summary.json").write_text(
        json.dumps({"args": vars(args), "repeats": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if all(bool(report["all_correct"]) for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
