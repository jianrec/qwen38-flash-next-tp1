#!/usr/bin/env python3
"""Correctness-first cache acceptance benchmark for the two-Spark SGLang setup.

Uses only the Python standard library. It can run on the Mac and collect host
metrics from both Spark nodes over SSH. Every request saves its raw output and
cache-tier details; a Mooncake L3 pass requires --require-storage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead
from pathlib import Path
from threading import Barrier, TIMEOUT_MAX
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# The canonical SSH aliases may not resolve when the benchmark runs on zj2.
SSH_FALLBACK_IPS = {"zj2": "10.240.23.1", "zj3": "10.240.23.2"}
REPLAY_TTFT_SLO = 5.0
REPLAY_TPOT_SLO = 0.1
DEFAULT_STORAGE_COVERAGE = 0.95


def now() -> float:
    return time.time()


def command_output(command: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ERROR {type(exc).__name__}: {exc}"
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\nSTDERR {result.stderr.strip()}".strip()
    return output


def raw_http(url: str, timeout: float = 10.0) -> str:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


def post_http(url: str, timeout: float = 620.0) -> dict[str, Any]:
    request = Request(url, data=b"", method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"ok": 200 <= response.status < 300, "status": response.status, "body": body}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "body": body}
    except (URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": None, "body": f"{type(exc).__name__}: {exc}"}


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree):\s+(\d+) kB", line)
        if match:
            values[match.group(1)] = int(match.group(2))
    if "MemTotal" in values and "MemAvailable" in values:
        values["MemUsedDerived"] = values["MemTotal"] - values["MemAvailable"]
    return values


def host_snapshot(node: str, run_id: str) -> dict[str, Any]:
    remote = (
        "cat /proc/meminfo; printf '\\n---FREE---\\n'; free -h; "
        "printf '\\n---DOCKER---\\n'; docker stats --no-stream; "
        "printf '\\n---NVIDIA---\\n'; nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader; "
        "printf '\\n---RUN_CONTAINERS---\\n'; "
        f"docker ps --filter name={run_id} --format '{{{{.Names}}}} {{{{.Status}}}}'"
    )
    command = ["bash", "-lc", remote] if node in ("local", "localhost") else ["ssh", node, remote]
    text = command_output(command)
    fallback_ip = SSH_FALLBACK_IPS.get(node)
    if (
        fallback_ip
        and ("Could not resolve hostname" in text or "Temporary failure in name resolution" in text)
    ):
        fallback_text = command_output(["ssh", fallback_ip, remote])
        text = f"SSH fallback {node}->{fallback_ip}\n{fallback_text}"
    return {
        "time": now(),
        "meminfo": parse_meminfo(text),
        "raw": text,
    }


def snapshot(nodes: list[str], run_id: str, phase: str) -> dict[str, Any]:
    return {"phase": phase, "time": now(), "nodes": {node: host_snapshot(node, run_id) for node in nodes}}


def make_code(tag: str, doc_id: int, position: str) -> str:
    digest = hashlib.sha256(f"{tag}|{doc_id}|{position}".encode()).hexdigest()[:8].upper()
    return f"CODE-{doc_id:04d}-{position[0].upper()}-{digest}"


def build_prefix(tag: str, doc_id: int, units: int) -> tuple[str, dict[str, str]]:
    codes = {position: make_code(tag, doc_id, position) for position in ("start", "middle", "end")}
    lines = [
        f"CACHE ACCEPTANCE DOCUMENT {doc_id:04d}; namespace {tag}.",
        f"START RECOVERY CODE: {codes['start']}",
    ]
    unit = f"cache_context_unit_{doc_id:04d} contains stable benchmark text. "
    for index in range(units):
        if index == units // 2:
            lines.append(f"MIDDLE RECOVERY CODE: {codes['middle']}")
        lines.append(unit)
    lines.append(f"END RECOVERY CODE: {codes['end']}")
    return "\n".join(lines), codes


def build_prompt(prefix: str, doc_id: int, position: str) -> str:
    return (
        prefix
        + f"\nFor CACHE ACCEPTANCE DOCUMENT {doc_id:04d}, what is the {position.upper()} RECOVERY CODE? "
        + "Respond with only that exact code and no other text."
    )


def parse_stream(
    url: str, payload: dict[str, Any], timeout: float, *, capture_sse_timing: bool = False
) -> dict[str, Any]:
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
    tier_details: dict[str, Any] = {}
    finish_reasons: list[str] = []
    error: str | None = None
    events = 0
    timing: dict[str, Any] | None = None
    if capture_sse_timing:
        timing = {
            "content_offsets_s": [],
            "finish_offsets_s": [],
            "usage_offsets_s": [],
            "done_offsets_s": [],
            "eof_offset_s": None,
        }

    try:
        with urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    if timing is not None:
                        timing["done_offsets_s"].append(time.perf_counter() - started)
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
                    if timing is not None:
                        timing["usage_offsets_s"].append(event_time - started)
                sglext = event.get("sglext")
                if isinstance(sglext, dict) and isinstance(sglext.get("cached_tokens_details"), dict):
                    tier_details = sglext["cached_tokens_details"]
                for choice in event.get("choices", []):
                    piece = choice.get("text")
                    if piece is None:
                        piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        if first_text is None:
                            first_text = event_time
                        text_parts.append(piece)
                        if timing is not None:
                            timing["content_offsets_s"].append(event_time - started)
                    if choice.get("finish_reason"):
                        finish_reasons.append(str(choice["finish_reason"]))
                        if timing is not None:
                            timing["finish_offsets_s"].append(event_time - started)
            if timing is not None:
                timing["eof_offset_s"] = time.perf_counter() - started
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        error = f"HTTPError {exc.code}: {detail[:4000]}"
    except (URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except IncompleteRead as exc:
        if timing is None:
            raise
        error = f"{type(exc).__name__}: {exc}"

    finished = time.perf_counter()
    elapsed = finished - started
    ttft = (first_text or first_event or finished) - started
    completion_tokens = usage.get("completion_tokens")
    prompt_tokens = usage.get("prompt_tokens")
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = details.get("cached_tokens", 0) or 0
    output_text = "".join(text_parts)
    if not isinstance(completion_tokens, int) or completion_tokens <= 0:
        completion_tokens = len(text_parts) if text_parts else 0
    decode_seconds = max(0.0, elapsed - ttft)
    result = {
        "started_at": now() - elapsed,
        "elapsed_s": elapsed,
        "ttft_s": ttft,
        "tpot_s": decode_seconds / max(1, completion_tokens - 1) if completion_tokens else None,
        "output_tok_per_s": completion_tokens / elapsed if elapsed > 0 else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cached_tokens_details": tier_details,
        "output_text": output_text,
        "output_sha256": hashlib.sha256(output_text.strip().encode()).hexdigest(),
        "finish_reasons": finish_reasons,
        "stream_events": events,
        "error": error,
    }
    if timing is not None:
        result["sse_timing"] = timing
    return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    good = [row for row in rows if not row.get("error")]

    def values(name: str) -> list[float]:
        return [float(row[name]) for row in good if row.get(name) is not None]

    def percentile(name: str, q: float) -> float | None:
        data = sorted(values(name))
        if not data:
            return None
        return data[min(len(data) - 1, max(0, round((len(data) - 1) * q)))]

    tier_totals = defaultdict(int)
    for row in rows:
        for tier, count in (row.get("cached_tokens_details") or {}).items():
            if isinstance(count, (int, float)):
                tier_totals[tier] += int(count)
    total_completion = sum(row.get("completion_tokens") or 0 for row in good)
    total_elapsed = sum(row.get("elapsed_s") or 0 for row in good)
    return {
        "requests": len(rows),
        "successful_requests": len(good),
        "failed_requests": len(rows) - len(good),
        "correct_answers": sum(bool(row.get("answer_correct")) for row in rows),
        "incorrect_answers": sum(row.get("answer_correct") is False for row in rows),
        "ttft_mean_s": statistics.mean(values("ttft_s")) if values("ttft_s") else None,
        "ttft_p50_s": percentile("ttft_s", 0.50),
        "ttft_p95_s": percentile("ttft_s", 0.95),
        "tpot_mean_s": statistics.mean(values("tpot_s")) if values("tpot_s") else None,
        "aggregate_output_tok_s": total_completion / total_elapsed if total_elapsed else None,
        "cache_tier_tokens": dict(tier_totals),
    }


def replay_paced(
    replay: Callable[[int], dict[str, Any]], order: list[int], workers: int, rate: float
) -> tuple[list[dict[str, Any]], float]:
    """Release a finite trace at fixed deadlines and record worker dispatch lag."""
    with ThreadPoolExecutor(max_workers=workers) as executor:
        started = time.perf_counter()

        def dispatch(doc_id: int, planned_offset: float) -> dict[str, Any]:
            actual_offset = time.perf_counter() - started
            jitter = actual_offset - planned_offset
            row = replay(doc_id)
            ttft = row.get("ttft_s")
            row.update(
                planned_dispatch_offset_s=planned_offset,
                actual_dispatch_offset_s=actual_offset,
                dispatch_jitter_s=jitter,
                schedule_to_first_token_s=max(jitter, 0.0) + ttft if ttft is not None else None,
            )
            return row

        pending = []
        for index, doc_id in enumerate(order):
            planned_offset = index / rate
            delay = started + planned_offset - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            pending.append(executor.submit(dispatch, doc_id, planned_offset))
        rows = [future.result() for future in pending]
        return rows, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://10.240.23.1:31000/v1/chat/completions")
    parser.add_argument("--metrics-url", default="http://10.240.23.1:31000/metrics")
    parser.add_argument("--mooncake-url", default="http://10.240.23.1:9003/metrics/summary")
    parser.add_argument("--model", default="qwen38-flash-next")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--nodes", default="zj2,zj3")
    parser.add_argument("--targets", type=int, default=4)
    parser.add_argument("--target-start", type=int, default=0)
    parser.add_argument("--pressure", type=int, default=60)
    parser.add_argument("--prefix-units", type=int, default=400)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--require-storage", action="store_true")
    parser.add_argument("--replay-concurrency", type=int, default=1, help="Maximum in-flight replay requests")
    parser.add_argument(
        "--replay-rate", type=float, default=argparse.SUPPRESS,
        help="Planned requests per second for finite replay",
    )
    parser.add_argument(
        "--replay-order-seed", type=int, default=argparse.SUPPRESS,
        help="Shuffle paced replay with this seed",
    )
    parser.add_argument(
        "--capture-replay-timing", action="store_true", default=argparse.SUPPRESS,
        help="Record replay SSE offsets and write before/after replay server metrics",
    )
    parser.add_argument("--min-storage-coverage", type=float, help="Minimum storage/prompt tokens per replay")
    parser.add_argument("--storage-backend", default="HiCacheFile", help="Backend for storage coverage and goodput")
    parser.add_argument("--require-host", action="store_true")
    parser.add_argument("--flush-before-replay", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=0.0)
    parser.add_argument("--require-full-replay-cache", action="store_true")
    parser.add_argument("--stop-after-pressure", action="store_true")
    args = parser.parse_args()

    if args.targets < 1 or args.target_start < 0 or args.pressure < 0 or args.prefix_units < 10:
        raise SystemExit("targets must be >=1, pressure >=0, prefix-units >=10")
    if args.replay_concurrency < 1:
        parser.error("--replay-concurrency must be positive")
    if args.min_storage_coverage is not None and not 0 < args.min_storage_coverage <= 1:
        parser.error("--min-storage-coverage must be in (0, 1]")
    replay_rate = getattr(args, "replay_rate", None)
    replay_order_seed = getattr(args, "replay_order_seed", None)
    capture_replay_timing = getattr(args, "capture_replay_timing", False)
    if capture_replay_timing and args.stop_after_pressure:
        parser.error("--capture-replay-timing requires replay")
    if replay_rate is not None:
        if not math.isfinite(replay_rate) or replay_rate <= 0:
            parser.error("--replay-rate must be positive and finite")
        if (args.targets - 1) / replay_rate > TIMEOUT_MAX:
            parser.error("--replay-rate is too small to schedule all targets")
        if args.stop_after_pressure:
            parser.error("--replay-rate cannot be used with --stop-after-pressure")
    elif replay_order_seed is not None:
        parser.error("--replay-order-seed requires --replay-rate")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes = [node.strip() for node in args.nodes.split(",") if node.strip()]
    payload_base = {
        "model": args.model,
        "stream": True,
        "stream_options": {"include_usage": True, "continuous_usage_stats": True},
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": args.max_new_tokens,
        "return_cached_tokens_details": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    prefixes: dict[int, str] = {}
    codes: dict[int, dict[str, str]] = {}
    for doc_id in range(args.target_start, args.target_start + args.targets + args.pressure):
        prefixes[doc_id], codes[doc_id] = build_prefix(args.tag, doc_id, args.prefix_units)

    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    reference_outputs: dict[int, str] = {}

    def record_row(row: dict[str, Any]) -> None:
        rows.append(row)
        print(json.dumps({
            "phase": row["phase"], "doc": row["doc_id"], "position": row["position"],
            "correct": row["answer_correct"], "ttft_s": round(row["ttft_s"], 3),
            "cache": row["cached_tokens_details"], "error": row["error"],
        }), flush=True)

    def run_request(doc_id: int, position: str, phase: str, *, record: bool = True) -> dict[str, Any]:
        payload = dict(payload_base)
        prompt = build_prompt(prefixes[doc_id], doc_id, position)
        payload["messages"] = [{"role": "user", "content": prompt}]
        if capture_replay_timing and phase == "target_replay":
            row = parse_stream(args.url, payload, args.timeout, capture_sse_timing=True)
        else:
            row = parse_stream(args.url, payload, args.timeout)
        expected = codes[doc_id][position]
        normalized = row["output_text"].strip()
        row.update(
            {
                "phase": phase,
                "doc_id": doc_id,
                "position": position,
                "expected_code": expected,
                "answer_correct": not row["error"] and expected in normalized,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            }
        )
        if record:
            record_row(row)
        return row

    snapshots.append(snapshot(nodes, args.run_id, "before"))
    for doc_id in range(args.target_start, args.target_start + args.targets):
        row = run_request(doc_id, "end", "target_cold")
        reference_outputs[doc_id] = row["output_text"].strip()
    snapshots.append(snapshot(nodes, args.run_id, "after_target_cold"))

    hot = run_request(args.target_start, "end", "target_hot")
    hot["exact_match_reference"] = hot["output_text"].strip() == reference_outputs[args.target_start]
    snapshots.append(snapshot(nodes, args.run_id, "after_target_hot"))

    for doc_id in range(args.target_start + args.targets, args.target_start + args.targets + args.pressure):
        run_request(doc_id, "end", "pressure_unique")
    snapshots.append(snapshot(nodes, args.run_id, "after_pressure"))

    if args.settle_seconds > 0 and not args.stop_after_pressure:
        print(json.dumps({"phase": "settle_after_pressure", "seconds": args.settle_seconds}), flush=True)
        time.sleep(args.settle_seconds)
        snapshots.append(snapshot(nodes, args.run_id, "after_pressure_settle"))

    flush_result = None
    if args.flush_before_replay and not args.stop_after_pressure:
        base_url = args.url.rsplit("/v1/", 1)[0]
        flush_result = post_http(base_url + "/flush_cache?timeout=600")
        print(json.dumps({"phase": "flush_cache", **flush_result}), flush=True)
        snapshots.append(snapshot(nodes, args.run_id, "after_flush"))
        if args.settle_seconds > 0:
            print(json.dumps({"phase": "settle_after_flush", "seconds": args.settle_seconds}), flush=True)
            time.sleep(args.settle_seconds)
            snapshots.append(snapshot(nodes, args.run_id, "after_flush_settle"))

    replay_order = [] if args.stop_after_pressure else list(range(args.target_start, args.target_start + args.targets))
    if len(replay_order) == 4:
        replay_order = [args.target_start + 2, args.target_start, args.target_start + 3, args.target_start + 1]
    if replay_order_seed is not None:
        random.Random(replay_order_seed).shuffle(replay_order)
    storage_threshold = args.min_storage_coverage or DEFAULT_STORAGE_COVERAGE
    replay_workers = min(args.replay_concurrency, len(replay_order))

    def replay(doc_id: int) -> dict[str, Any]:
        row = run_request(doc_id, "end", "target_replay", record=False)
        output = row["output_text"].strip()
        row["exact_match_reference"] = output == reference_outputs[doc_id]
        row["exact_answer"] = not row["error"] and output == row["expected_code"] and row["exact_match_reference"]
        details = row["cached_tokens_details"]
        storage = details.get("storage") or 0
        prompt_tokens = row["prompt_tokens"]
        row["storage_coverage"] = storage / prompt_tokens if prompt_tokens else 0.0
        row["storage_qualified"] = (
            not row["error"]
            and details.get("storage_backend") == args.storage_backend
            and storage_threshold <= row["storage_coverage"] <= 1.0
        )
        return row

    if capture_replay_timing:
        (output_dir / "server_metrics_before_replay.txt").write_text(raw_http(args.metrics_url), encoding="utf-8")
    replay_started = time.perf_counter()
    if replay_rate is not None:
        replay_rows, replay_wall = replay_paced(replay, replay_order, replay_workers, replay_rate)
    elif replay_workers > 1:
        def mark_replay_start() -> None:
            nonlocal replay_started
            replay_started = time.perf_counter()

        ready = Barrier(replay_workers + 1, action=mark_replay_start, timeout=30)

        def submit_replay(index_doc: tuple[int, int]) -> dict[str, Any]:
            index, doc_id = index_doc
            if index < replay_workers:
                ready.wait()
            return replay(doc_id)

        with ThreadPoolExecutor(max_workers=replay_workers) as executor:
            pending = executor.map(submit_replay, enumerate(replay_order))
            ready.wait()
            replay_rows = list(pending)
            replay_wall = time.perf_counter() - replay_started
    else:
        replay_rows = [replay(doc_id) for doc_id in replay_order]
        replay_wall = time.perf_counter() - replay_started
    if capture_replay_timing:
        (output_dir / "server_metrics_after_replay.txt").write_text(raw_http(args.metrics_url), encoding="utf-8")
    for row in replay_rows:
        record_row(row)
    snapshots.append(snapshot(nodes, args.run_id, "after_replay"))

    if not args.stop_after_pressure:
        for position in ("start", "middle"):
            run_request(args.target_start, position, f"position_{position}")
    snapshots.append(snapshot(nodes, args.run_id, "after_position_checks"))

    by_phase = {
        phase: summarize([row for row in rows if row["phase"] == phase])
        for phase in dict.fromkeys(row["phase"] for row in rows)
    }
    replay_storage = by_phase.get("target_replay", {}).get("cache_tier_tokens", {}).get("storage", 0)
    replay_host = by_phase.get("target_replay", {}).get("cache_tier_tokens", {}).get("host", 0)
    replay_serving = None
    if replay_rows:
        successful = [row for row in replay_rows if not row["error"]]
        slo_rows = [
            row for row in successful
            if row["exact_answer"] and row["ttft_s"] <= REPLAY_TTFT_SLO
            and row["tpot_s"] is not None and row["tpot_s"] <= REPLAY_TPOT_SLO
        ]
        storage_slo_count = sum(row["storage_qualified"] for row in slo_rows)
        replay_serving = {
            "client_concurrency": replay_workers,
            "wall_s": replay_wall,
            "wallclock_req_s": len(successful) / replay_wall,
            "wallclock_output_tok_s": sum(row["completion_tokens"] for row in successful) / replay_wall,
            "slo_threshold": {"ttft_s": REPLAY_TTFT_SLO, "tpot_s": REPLAY_TPOT_SLO},
            "slo_goodput_requests": len(slo_rows),
            "slo_goodput_req_s": len(slo_rows) / replay_wall,
            "storage_backend": args.storage_backend,
            "min_storage_coverage": storage_threshold,
            "storage_qualified_requests": sum(row["storage_qualified"] for row in successful),
            "storage_qualified_slo_requests": storage_slo_count,
            "storage_qualified_slo_goodput_req_s": storage_slo_count / replay_wall,
        }
        if replay_rate is not None:
            scheduled_storage_slo_count = sum(
                row["storage_qualified"]
                and row["schedule_to_first_token_s"] is not None
                and row["schedule_to_first_token_s"] <= REPLAY_TTFT_SLO
                for row in slo_rows
            )
            replay_serving.update(
                dispatch_mode="paced",
                offered_rate_req_s=replay_rate,
                scheduled_arrival_storage_qualified_slo_requests=scheduled_storage_slo_count,
                scheduled_arrival_storage_qualified_slo_goodput_req_s=scheduled_storage_slo_count / replay_wall,
            )
        if replay_workers > 1 or replay_rate is not None:
            by_phase["target_replay"]["aggregate_output_tok_s"] = replay_serving["wallclock_output_tok_s"]
    full_replay_cache = len(replay_rows) == args.targets and all(
        row.get("prompt_tokens")
        and (row.get("cached_tokens") or 0) / row["prompt_tokens"] >= DEFAULT_STORAGE_COVERAGE
        for row in replay_rows
    )
    incorrect = [row for row in rows if not row["answer_correct"]]
    exact_mismatches = [
        row for row in rows
        if row["phase"] in ("target_hot", "target_replay") and row.get("exact_match_reference") is False
    ]
    memory_summary: dict[str, Any] = {}
    memory_ok = True
    swap_ok = True
    for node in nodes:
        node_snapshots = [entry["nodes"][node] for entry in snapshots]
        available = [entry["meminfo"].get("MemAvailable") for entry in node_snapshots]
        available = [value for value in available if value is not None]
        used = [entry["meminfo"].get("MemUsedDerived") for entry in node_snapshots]
        used = [value for value in used if value is not None]
        swap_used = [
            entry["meminfo"]["SwapTotal"] - entry["meminfo"]["SwapFree"]
            for entry in node_snapshots
            if "SwapTotal" in entry["meminfo"] and "SwapFree" in entry["meminfo"]
        ]
        if not len(available) == len(used) == len(swap_used) == len(snapshots):
            memory_ok = False
            swap_ok = False
            memory_summary[node] = {"error": "missing meminfo fields"}
            continue
        min_available = min(available)
        swap_growth = max(0, max(swap_used) - swap_used[0])
        memory_summary[node] = {
            "min_mem_available_kib": min_available,
            "min_mem_available_gib": min_available / 1024 / 1024,
            "start_mem_used_gib": used[0] / 1024 / 1024,
            "end_mem_used_gib": used[-1] / 1024 / 1024,
            "max_mem_used_gib": max(used) / 1024 / 1024,
            "max_swap_growth_kib": swap_growth,
        }
        memory_ok = memory_ok and min_available >= 16 * 1024 * 1024
        swap_ok = swap_ok and swap_growth <= 1024 * 1024
    gates = {
        "all_requests_successful": all(not row["error"] for row in rows),
        "all_answers_correct": not incorrect,
        "cold_hot_replay_outputs_exact": not exact_mismatches,
        "uma_headroom_at_least_16_gib": memory_ok,
        "swap_growth_at_most_1_gib": swap_ok,
        "flush_cache_succeeded": flush_result["ok"] if flush_result is not None else None,
        "storage_hit_required_and_observed": replay_storage > 0 if args.require_storage else None,
        "all_replays_meet_storage_coverage": (
            len(replay_rows) == args.targets and all(row["storage_qualified"] for row in replay_rows)
            if args.min_storage_coverage is not None else None
        ),
        "all_replay_answers_exact": (
            len(replay_rows) == args.targets and all(row["exact_answer"] for row in replay_rows)
            if args.replay_concurrency > 1 or args.min_storage_coverage is not None else None
        ),
        "full_replay_cache_required_and_observed": full_replay_cache if args.require_full_replay_cache else None,
        "host_hit_required_and_observed": replay_host > 0 if args.require_host else None,
    }
    summary_all = summarize(rows)
    if replay_workers > 1 or replay_rate is not None:
        # Use replay_serving when overlap or pacing makes summed request times misleading.
        summary_all["aggregate_output_tok_s"] = None
    report = {
        "config": vars(args),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "created_at": now(),
        "rows": rows,
        "summary_all": summary_all,
        "by_phase": by_phase,
        "replay_serving": replay_serving,
        "memory_summary": memory_summary,
        "flush_result": flush_result,
        "gates": gates,
        "incorrect_rows": [{"phase": row["phase"], "doc_id": row["doc_id"], "expected": row["expected_code"], "output": row["output_text"], "error": row["error"]} for row in incorrect],
        "exact_mismatch_rows": [{"phase": row["phase"], "doc_id": row["doc_id"], "output": row["output_text"]} for row in exact_mismatches],
        "snapshots": snapshots,
        "server_metrics": raw_http(args.metrics_url),
        "mooncake_metrics": raw_http(args.mooncake_url),
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        with (output_dir / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = sorted({key for row in rows for key in row})
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (output_dir / "server_metrics.txt").write_text(report["server_metrics"], encoding="utf-8")
    (output_dir / "mooncake_metrics.txt").write_text(report["mooncake_metrics"], encoding="utf-8")
    passed = all(value is not False for value in gates.values())
    (output_dir / "status.json").write_text(json.dumps({"passed": passed, "gates": gates}, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": passed, "gates": gates, "by_phase": by_phase, "replay_serving": replay_serving,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
