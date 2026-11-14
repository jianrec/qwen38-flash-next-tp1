import json
import sys
import time
import urllib.request

output_path = sys.argv[1]
request_data = {
    "model": "qwen38-flash-next",
    "messages": [{"role": "user", "content": "只回答数字：1+1等于几？"}],
    "max_tokens": 64,
    "temperature": 0,
    "chat_template_kwargs": {"enable_thinking": False},
}
record = {"request": request_data}
started = time.monotonic()
try:
    request = urllib.request.Request(
        "http://127.0.0.1:32000/v1/chat/completions",
        data=json.dumps(request_data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        record["http_status"] = response.status
        raw = response.read().decode("utf-8")
    record["response"] = json.loads(raw)
except Exception as error:
    record["error"] = f"{type(error).__name__}: {error}"
record["elapsed_seconds"] = time.monotonic() - started

with open(output_path, "x", encoding="utf-8") as output:
    json.dump(record, output, ensure_ascii=False, indent=2)

if "error" in record:
    raise SystemExit(record["error"])
response = record["response"]
content = response["choices"][0]["message"].get("content") or ""
print(json.dumps({"content": content, "elapsed_seconds": record["elapsed_seconds"], "usage": response.get("usage")}, ensure_ascii=False))
if record["http_status"] != 200 or "2" not in content:
    raise SystemExit("smoke response did not contain the expected answer: 2")
