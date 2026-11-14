#!/usr/bin/env python3
"""Check first L3 reuse of real chat histories after unrelated cache pressure."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_acceptance as bench


TOKENIZE = """
import json
import sys
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(sys.argv[1], local_files_only=True, trust_remote_code=True)
result = []
for messages, add_generation_prompt in json.load(sys.stdin):
    encoded = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=add_generation_prompt, enable_thinking=False
    )
    result.append(list(encoded.input_ids if hasattr(encoded, 'input_ids') else encoded))
print(json.dumps(result))
"""


def render_prompts(args, prompts):
    command = [sys.executable]
    if args.tokenizer_container:
        command = ["docker", "exec", "-i", args.tokenizer_container, "/opt/sglang/bin/python3", "-I"]
    result = subprocess.run(
        [*command, "-B", "-c", TOKENIZE, args.tokenizer],
        input=json.dumps(prompts), text=True, capture_output=True, timeout=args.timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Tokenizer exited {result.returncode}: {result.stderr[-2000:]}")
    rendered = json.loads(result.stdout)
    if len(rendered) != len(prompts):
        raise ValueError("Tokenizer output does not match the prompt batch")
    return rendered


def common_prefix_length(left, right):
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def trace_identity(rows):
    return [
        (row["phase"], row["doc_id"], row["prompt_sha256"], row["prompt_tokens"], row["output_text"].strip())
        for row in rows
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True, help="Local tokenizer for the served checkpoint")
    parser.add_argument("--tokenizer-container", help="Use the existing rank container to render prompts after measurement")
    parser.add_argument("--url", default="http://127.0.0.1:31000/v1/chat/completions")
    parser.add_argument("--model", default="qwen38-flash-next")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tag", required=True, help="Same tag for matched controls in separate cache namespaces")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--nodes", default="localhost,node-1")
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--pressure", type=int, default=56)
    parser.add_argument("--prefix-units", type=int, default=400)
    parser.add_argument("--settle-seconds", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--storage-backend", choices=("HiCacheFile", "MooncakeWholePageStore", "GPU-only"), default="HiCacheFile")
    parser.add_argument("--reference-report", type=Path, help="Require an exact ordered trace match to this report")
    args = parser.parse_args()
    if not 2 <= args.sessions <= 8 or args.pressure < 1 or args.prefix_units < 10:
        parser.error("need 2–8 sessions, at least one pressure document and at least 10 prefix units")
    if not math.isfinite(args.settle_seconds) or args.settle_seconds < 0:
        parser.error("--settle-seconds must be nonnegative and finite")
    nodes = [node.strip() for node in args.nodes.split(",") if node.strip()]
    if len(nodes) != 2 or len(set(nodes)) != 2:
        parser.error("--nodes must specify two distinct snapshot sources")

    reference = json.loads(args.reference_report.read_text())["rows"] if args.reference_report else None
    args.output.mkdir(parents=True)
    metrics_url = args.url.rsplit("/v1/", 1)[0] + "/metrics"
    payload = {
        "model": args.model,
        "stream": True,
        "stream_options": {"include_usage": True, "continuous_usage_stats": True},
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 64,
        "return_cached_tokens_details": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    rows = []
    sessions = []
    snapshots = [bench.snapshot(nodes, args.run_id, "before")]

    def request(doc_id, phase, messages, expected):
        row = bench.parse_stream(args.url, {**payload, "messages": messages}, args.timeout)
        row.update(
            doc_id=doc_id,
            phase=phase,
            expected_code=expected,
            answer_correct=not row["error"] and row["output_text"].strip() == expected,
            prompt_sha256=hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest(),
        )
        return row

    for doc_id in range(args.sessions + args.pressure):
        prefix, codes = bench.build_prefix(args.tag, doc_id, args.prefix_units)
        initial = doc_id < args.sessions
        position = "start" if initial else "end"
        messages = [{"role": "user", "content": bench.build_prompt(prefix, doc_id, position)}]
        row = request(doc_id, "first_turn" if initial else "pressure_unique", messages, codes[position])
        rows.append(row)
        if initial:
            sessions.append((row, messages, codes["end"]))

    snapshots.append(bench.snapshot(nodes, args.run_id, "after_pressure"))
    time.sleep(args.settle_seconds)
    metrics_before = bench.raw_http(metrics_url)
    started = 0.0

    def mark_start():
        nonlocal started
        started = time.perf_counter()

    barrier = Barrier(args.sessions + 1, action=mark_start, timeout=30)

    def followup(session):
        initial, first_messages, expected = session
        doc_id = initial["doc_id"]
        history = [*first_messages, {"role": "assistant", "content": initial["output_text"]}]
        followup_message = {
            "role": "user",
            "content": f"Using the document in this conversation, what is the END RECOVERY CODE? "
            f"Respond with only the exact code for document {doc_id:04d} and no other text.",
        }
        messages = [*history, followup_message]
        barrier.wait()
        row = request(doc_id, "followup", messages, expected)
        row["messages"] = messages
        return row

    with ThreadPoolExecutor(max_workers=args.sessions) as executor:
        pending = executor.map(followup, sessions)
        barrier.wait()
        followups = list(pending)
        wall = time.perf_counter() - started
    metrics_after = bench.raw_http(metrics_url)
    snapshots.append(bench.snapshot(nodes, args.run_id, "after_followup"))

    templates = []
    for (_, first_messages, _), row in zip(sessions, followups, strict=True):
        messages = row["messages"]
        templates.extend(((first_messages, True), (messages[:-1], False), (messages, True)))
    tokenizer_error = None
    try:
        rendered = render_prompts(args, templates)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        tokenizer_error = str(exc)
        rendered = [[]] * len(templates)

    for index, ((initial, _, _), row) in enumerate(zip(sessions, followups, strict=True)):
        first_input, prior_history, followup_input = rendered[index * 3:index * 3 + 3]
        first_shared = common_prefix_length(first_input, followup_input)
        reusable = min(
            common_prefix_length(prior_history, followup_input),
            (initial["prompt_tokens"] or 0) + (initial["completion_tokens"] or 0),
        )
        geometry_verified = (
            0 < len(first_input) == initial["prompt_tokens"]
            and len(followup_input) == row["prompt_tokens"]
            and first_shared >= bench.DEFAULT_STORAGE_COVERAGE * len(first_input)
            and reusable > 0
        )
        details = row["cached_tokens_details"]
        storage = details.get("storage") or 0
        coverage = storage / reusable if reusable else None
        row.update(
            rendered_first_turn_tokens=len(first_input),
            rendered_followup_tokens=len(followup_input),
            shared_first_turn_input_tokens=first_shared,
            reusable_prefix_tokens=reusable,
            prompt_geometry_verified=geometry_verified,
            storage_coverage=coverage,
            storage_qualified=geometry_verified
            and row["answer_correct"]
            and details.get("storage_backend") == args.storage_backend
            and coverage is not None
            and bench.DEFAULT_STORAGE_COVERAGE <= coverage <= 1.0,
            slo_passed=row["answer_correct"]
            and row["ttft_s"] is not None and row["ttft_s"] <= bench.REPLAY_TTFT_SLO
            and row["tpot_s"] is not None and row["tpot_s"] <= bench.REPLAY_TPOT_SLO,
        )
    rows.extend(followups)

    memory = [sample["nodes"][node]["meminfo"] for sample in snapshots for node in nodes]
    gates = {
        "all_answers_exact": all(row["answer_correct"] for row in rows),
        "first_turns_uncached": all(initial["cached_tokens"] == 0 for initial, _, _ in sessions),
        "followup_token_geometry_verified": all(row["prompt_geometry_verified"] for row in followups),
        "reference_trace_exact": trace_identity(rows) == trace_identity(reference) if reference is not None else None,
        "uma_headroom_at_least_16_gib": all(m.get("MemAvailable", 0) >= 16 * 1024**2 for m in memory),
    }
    for node in nodes:
        samples = [sample["nodes"][node]["meminfo"] for sample in snapshots]
        swap = [sample["SwapTotal"] - sample["SwapFree"] for sample in samples if {"SwapTotal", "SwapFree"} <= sample.keys()]
        gates[f"{node}_swap_growth_at_most_1_gib"] = len(swap) == len(samples) and max(swap) - swap[0] <= 1024**2
    if args.storage_backend == "GPU-only":
        gates["gpu_only_no_host_or_storage"] = all(
            not any(row["cached_tokens_details"].get(tier) for tier in ("host", "storage")) for row in rows
        )
    else:
        gates["all_followups_meet_storage_coverage"] = all(row["storage_qualified"] for row in followups)

    slo = sum(row["slo_passed"] for row in followups)
    storage_slo = sum(row["storage_qualified"] and row["slo_passed"] for row in followups)
    serving = {
        "client_concurrency": args.sessions,
        "wall_s": wall,
        "wallclock_req_s": sum(not row["error"] for row in followups) / wall,
        "wallclock_output_tok_s": sum(row["completion_tokens"] for row in followups if not row["error"]) / wall,
        "slo_threshold": {"ttft_s": bench.REPLAY_TTFT_SLO, "tpot_s": bench.REPLAY_TPOT_SLO},
        "slo_goodput_requests": slo,
        "slo_goodput_req_s": slo / wall,
        "min_storage_coverage": bench.DEFAULT_STORAGE_COVERAGE,
        "storage_qualified_requests": sum(row["storage_qualified"] for row in followups),
        "storage_qualified_slo_requests": storage_slo,
        "storage_qualified_slo_goodput_req_s": storage_slo / wall,
    }
    passed = all(value is not False for value in gates.values())
    report = {
        "passed": passed,
        "config": {**vars(args), "output": str(args.output), "reference_report": str(args.reference_report) if reference is not None else None},
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "core_benchmark_sha256": hashlib.sha256(Path(bench.__file__).read_bytes()).hexdigest(),
        "coverage_denominator": "Common token prefix of prior serialized chat history and follow-up input, capped by first-turn prompt plus completion tokens.",
        "tokenizer_error": tokenizer_error,
        "gates": gates,
        "rows": rows,
        "replay_serving": serving,
        "snapshots": snapshots,
        "server_metrics_before": metrics_before,
        "server_metrics_after": metrics_after,
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": passed, "gates": gates, "replay_serving": serving}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
