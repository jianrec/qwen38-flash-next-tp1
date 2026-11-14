#!/usr/bin/env python3
"""Fixed MMLU/GSM8K quality comparison for cache-precision experiments."""

from __future__ import annotations

import argparse
import csv
import decimal
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def http_chat(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = data["choices"][0]["message"].get("content") or ""
        return {"text": text, "elapsed_s": time.perf_counter() - started, "error": None, "usage": data.get("usage", {})}
    except (HTTPError, URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as exc:
        return {"text": "", "elapsed_s": time.perf_counter() - started, "error": f"{type(exc).__name__}: {exc}", "usage": {}}


def parse_letter(text: str) -> str | None:
    match = re.search(r"\b([A-D])\b", text)
    return match.group(1) if match else None


def parse_number(text: str) -> str | None:
    if "####" in text:
        text = text.rsplit("####", 1)[1]
    matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not matches:
        return None
    return matches[-1].replace(",", "")


def numeric_equal(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return decimal.Decimal(left) == decimal.Decimal(right)
    except decimal.InvalidOperation:
        return False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:31000/v1/chat/completions")
    parser.add_argument("--model", default="qwen38-flash-next")
    parser.add_argument("--mmlu-dir", required=True)
    parser.add_argument("--gsm8k", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mmlu-per-subject", type=int, default=20)
    parser.add_argument("--gsm-count", type=int, default=50)
    parser.add_argument("--max-mmlu-tokens", type=int, default=8)
    parser.add_argument("--max-gsm-tokens", type=int, default=160)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--baseline-report")
    args = parser.parse_args()

    mmlu_dir = Path(args.mmlu_dir)
    gsm_path = Path(args.gsm8k)
    tasks: list[dict[str, Any]] = []
    dataset_hashes: dict[str, str] = {}
    for path in sorted(mmlu_dir.glob("*_test.csv")):
        dataset_hashes[path.name] = sha256(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for index, row in enumerate(csv.reader(handle)):
                if index >= args.mmlu_per_subject:
                    break
                question, a, b, c, d, expected = row
                prompt = (
                    "Answer the multiple-choice question with only the letter A, B, C, or D.\n"
                    f"Question: {question}\nA. {a}\nB. {b}\nC. {c}\nD. {d}\nAnswer:"
                )
                tasks.append({"kind": "mmlu", "id": f"{path.stem}:{index}", "prompt": prompt, "expected": expected})
    dataset_hashes[gsm_path.name] = sha256(gsm_path)
    with gsm_path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= args.gsm_count:
                break
            item = json.loads(line)
            expected = item["answer"].rsplit("####", 1)[1].strip().replace(",", "")
            prompt = (
                "Solve the math word problem. Give a short calculation and finish with "
                "'#### <number>'.\n"
                f"Problem: {item['question']}\n"
            )
            tasks.append({"kind": "gsm8k", "id": f"gsm8k:{index}", "prompt": prompt, "expected": expected})
    if not tasks:
        raise SystemExit("no tasks loaded")

    rows: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        max_tokens = args.max_mmlu_tokens if task["kind"] == "mmlu" else args.max_gsm_tokens
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": task["prompt"]}],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        result = http_chat(args.url, payload, args.timeout)
        text = result.pop("text")
        parsed = parse_letter(text) if task["kind"] == "mmlu" else parse_number(text)
        correct = parsed == task["expected"] if task["kind"] == "mmlu" else numeric_equal(parsed, task["expected"])
        row = {**task, **result, "output_text": text, "parsed": parsed, "correct": correct}
        rows.append(row)
        if (index + 1) % 20 == 0 or index + 1 == len(tasks):
            print(json.dumps({"completed": index + 1, "total": len(tasks), "kind": task["kind"], "correct": correct}), flush=True)

    by_kind: dict[str, Any] = {}
    for kind in ("mmlu", "gsm8k"):
        subset = [row for row in rows if row["kind"] == kind]
        correct = sum(bool(row["correct"]) for row in subset)
        parsed = sum(row["parsed"] is not None for row in subset)
        by_kind[kind] = {
            "tasks": len(subset),
            "correct": correct,
            "accuracy": correct / len(subset) if subset else None,
            "parsed": parsed,
            "errors": sum(bool(row["error"]) for row in subset),
        }

    baseline_comparison = None
    quality_gates = None
    if args.baseline_report:
        baseline = json.loads(Path(args.baseline_report).read_text())
        baseline_rows = {row["id"]: row for row in baseline["rows"]}
        changes = []
        for row in rows:
            old = baseline_rows.get(row["id"])
            if old is None:
                continue
            changes.append({
                "id": row["id"], "kind": row["kind"],
                "baseline_correct": old["correct"], "current_correct": row["correct"],
                "baseline_output_sha256": hashlib.sha256(old["output_text"].encode()).hexdigest(),
                "current_output_sha256": hashlib.sha256(row["output_text"].encode()).hexdigest(),
                "baseline_parsed": old["parsed"], "current_parsed": row["parsed"],
            })
        drops = {
            kind: (baseline["by_kind"][kind]["accuracy"] - by_kind[kind]["accuracy"]) * 100
            for kind in ("mmlu", "gsm8k")
        }
        baseline_comparison = {"accuracy_drop_percentage_points": drops, "rows": changes}
        quality_gates = {
            "mmlu_drop_at_most_2_pp": drops["mmlu"] <= 2.0,
            "gsm8k_drop_at_most_2_pp": drops["gsm8k"] <= 2.0,
            "no_accuracy_drop_below_baseline_over_2_pp": all(
                not (change["baseline_correct"] and not change["current_correct"])
                or drops[change["kind"]] <= 2.0
                for change in changes
            ),
        }

    report = {
        "config": vars(args),
        "created_at": time.time(),
        "dataset_hashes": dataset_hashes,
        "by_kind": by_kind,
        "overall": {
            "tasks": len(rows),
            "correct": sum(bool(row["correct"]) for row in rows),
            "accuracy": sum(bool(row["correct"]) for row in rows) / len(rows),
            "errors": sum(bool(row["error"]) for row in rows),
        },
        "rows": rows,
        "baseline_comparison": baseline_comparison,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = all(row["error"] is None and row["parsed"] is not None for row in rows)
    if quality_gates is not None:
        passed = passed and all(quality_gates.values())
    status = {"passed": passed, "by_kind": by_kind, "quality_gates": quality_gates}
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
