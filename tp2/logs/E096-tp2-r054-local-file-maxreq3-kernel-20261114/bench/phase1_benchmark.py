#!/usr/bin/env python3
"""Measure a Qwen/SGLang cache configuration through the OpenAI stream API.

The script keeps the workload deterministic and records per-request timing,
OpenAI usage, SGLang cache-tier details, and raw metric snapshots. It uses only
the Python standard library so it can run on the Spark host.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def now() -> float:
    return time.time()


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def raw_http(url: str, timeout: float = 5.0) -> str:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


def command_output(command: list[str], timeout: float = 10.0) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ERROR {type(exc).__name__}: {exc}"
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\nSTDERR {result.stderr.strip()}".strip()
    return output


def host_snapshot(container: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "time": now(),
        "free_h": command_output(["free", "-h"]),
        "docker_stats": command_output(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}\t{{.NetIO}}",
            ]
        ),
        "container_stats": command_output(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}\t{{.NetIO}}",
                container,
            ]
        ),
        "nvidia_smi": command_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ]
        ),
    }
    return snapshot


def build_prefix(word_count: int, tag: str) -> str:
    unit = "cache_context_unit_0001 contains stable benchmark text. "
    return (
        f"This is prefix-cache benchmark {tag}. Reuse the context "
        "before the request-specific suffix.\n" + unit * word_count
    )


def build_prompt(prefix: str, request_id: int, unique: bool) -> str:
    if unique:
        prefix = prefix.replace("0001", f"{request_id:04d}")
    suffix = (
        f"\nRequest id {request_id}. Summarize the cached context in one short "
        "sentence and end with the request id."
    )
    return prefix + suffix


def parse_stream(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.perf_counter()
    first_event: float | None = None
    first_text: float | None = None
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    sglext: dict[str, Any] = {}
    events = 0
    error: str | None = None

    try:
        with urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                events += 1
                event_time = time.perf_counter()
                if first_event is None:
                    first_event = event_time
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                if isinstance(event.get("sglext"), dict):
                    sglext = event["sglext"]
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or delta.get("reasoning_content") or ""
                    if piece:
                        if first_text is None:
                            first_text = event_time
                        text_parts.append(piece)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    finished = time.perf_counter()
    elapsed = finished - started
    ttft = (first_text or first_event or finished) - started
    completion_tokens = usage.get("completion_tokens")
    prompt_tokens = usage.get("prompt_tokens")
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = details.get("cached_tokens", 0) or 0
    tier_details = sglext.get("cached_tokens_details") or {}
    output_text = "".join(text_parts)
    if not isinstance(completion_tokens, int) or completion_tokens <= 0:
        completion_tokens = max(1, len(text_parts)) if text_parts else 0
    if not isinstance(prompt_tokens, int):
        prompt_tokens = None
    decode_seconds = max(0.0, elapsed - ttft)
    tpot = decode_seconds / max(1, completion_tokens - 1) if completion_tokens else None
    throughput = completion_tokens / elapsed if elapsed > 0 else None
    hit_rate = cached_tokens / prompt_tokens if prompt_tokens else None
    return {
        "started_at": now() - elapsed,
        "elapsed_s": elapsed,
        "ttft_s": ttft,
        "tpot_s": tpot,
        "output_tok_per_s": throughput,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_hit_rate": hit_rate,
        "cached_tokens_details": tier_details,
        "output_chars": len(output_text),
        "stream_events": events,
        "error": error,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    good = [row for row in rows if not row.get("error")]

    def values(name: str) -> list[float]:
        return [float(row[name]) for row in good if row.get(name) is not None]

    def mean(name: str) -> float | None:
        data = values(name)
        return statistics.mean(data) if data else None

    def percentile(name: str, q: float) -> float | None:
        data = sorted(values(name))
        if not data:
            return None
        index = min(len(data) - 1, max(0, round((len(data) - 1) * q)))
        return data[index]

    total_prompt = sum(row.get("prompt_tokens") or 0 for row in good)
    total_cached = sum(row.get("cached_tokens") or 0 for row in good)
    total_completion = sum(row.get("completion_tokens") or 0 for row in good)
    total_elapsed = sum(row.get("elapsed_s") or 0.0 for row in good)
    return {
        "requests": len(rows),
        "successful_requests": len(good),
        "failed_requests": len(rows) - len(good),
        "ttft_mean_s": mean("ttft_s"),
        "ttft_p50_s": percentile("ttft_s", 0.50),
        "ttft_p95_s": percentile("ttft_s", 0.95),
        "tpot_mean_s": mean("tpot_s"),
        "throughput_mean_tok_s": mean("output_tok_per_s"),
        "aggregate_output_tok_s": total_completion / total_elapsed if total_elapsed else None,
        "prompt_tokens": total_prompt,
        "cached_tokens": total_cached,
        "cache_hit_rate": total_cached / total_prompt if total_prompt else None,
        "completion_tokens": total_completion,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:31000/v1/chat/completions")
    parser.add_argument("--model", default="qwen38-flash-next")
    parser.add_argument("--output", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--measured", type=int, default=8)
    parser.add_argument("--prefix-units", type=int, default=400)
    parser.add_argument("--prefix-tag", default="phase1")
    parser.add_argument("--pressure-unique", type=int, default=0)
    parser.add_argument("--replay-shared", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = build_prefix(args.prefix_units, args.prefix_tag)
    payload_base = {
        "model": args.model,
        "stream": True,
        "stream_options": {"include_usage": True, "continuous_usage_stats": True},
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": args.max_new_tokens,
        "return_cached_tokens_details": True,
    }

    start_snapshot = host_snapshot(args.container)
    health = raw_http(args.url.rsplit("/v1/", 1)[0] + "/health")
    rows: list[dict[str, Any]] = []

    for index in range(args.warmup):
        payload = dict(payload_base)
        payload["messages"] = [{"role": "user", "content": build_prompt(prefix, index, False)}]
        result = parse_stream(args.url, payload, args.timeout)
        result.update({"phase": "warmup", "request_id": index})
        rows.append(result)

    if args.pressure_unique > 0:
        for index in range(args.pressure_unique):
            payload = dict(payload_base)
            payload["messages"] = [
                {"role": "user", "content": build_prompt(prefix, 3000 + index, True)}
            ]
            result = parse_stream(args.url, payload, args.timeout)
            result.update({"phase": "pressure_unique", "request_id": 3000 + index})
            rows.append(result)

        for index in range(args.replay_shared):
            payload = dict(payload_base)
            payload["messages"] = [
                {"role": "user", "content": build_prompt(prefix, 4000 + index, False)}
            ]
            result = parse_stream(args.url, payload, args.timeout)
            result.update({"phase": "replay_shared", "request_id": 4000 + index})
            rows.append(result)
    else:
        for index in range(args.measured):
            payload = dict(payload_base)
            payload["messages"] = [
                {"role": "user", "content": build_prompt(prefix, 1000 + index, False)}
            ]
            result = parse_stream(args.url, payload, args.timeout)
            result.update({"phase": "shared_prefix", "request_id": 1000 + index})
            rows.append(result)

        for index in range(max(2, args.measured // 2)):
            payload = dict(payload_base)
            payload["messages"] = [
                {"role": "user", "content": build_prompt(prefix, 2000 + index, True)}
            ]
            result = parse_stream(args.url, payload, args.timeout)
            result.update({"phase": "unique_prefix", "request_id": 2000 + index})
            rows.append(result)

    end_snapshot = host_snapshot(args.container)
    metrics = {
        "health": health,
        "server_metrics": raw_http("http://127.0.0.1:31000/metrics"),
        "mooncake_metrics": raw_http("http://127.0.0.1:9003/metrics/summary"),
    }
    report = {
        "config": vars(args),
        "workload": {
            "prefix_units": args.prefix_units,
            "warmup_requests": args.warmup,
            "measured_shared_prefix_requests": args.measured,
            "unique_prefix_requests": max(2, args.measured // 2),
            "pressure_unique_requests": args.pressure_unique,
            "replay_shared_requests": args.replay_shared,
            "max_new_tokens": args.max_new_tokens,
        },
        "start_snapshot": start_snapshot,
        "end_snapshot": end_snapshot,
        "summary_all": summarize(rows),
        "summary_measured": summarize([r for r in rows if r["phase"] != "warmup"]),
        "summary_shared_prefix": summarize([r for r in rows if r["phase"] == "shared_prefix"]),
        "summary_unique_prefix": summarize([r for r in rows if r["phase"] == "unique_prefix"]),
        "summary_pressure_unique": summarize([r for r in rows if r["phase"] == "pressure_unique"]),
        "summary_replay_shared": summarize([r for r in rows if r["phase"] == "replay_shared"]),
        "rows": rows,
        "metrics": metrics,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(output_dir / "requests.csv", rows)
    print(json.dumps({key: report[key] for key in (
        "summary_measured", "summary_shared_prefix", "summary_unique_prefix",
        "summary_pressure_unique", "summary_replay_shared"
    )}, ensure_ascii=False, indent=2))
    return 0 if report["summary_measured"]["failed_requests"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
