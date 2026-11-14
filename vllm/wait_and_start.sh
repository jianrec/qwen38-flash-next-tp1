#!/usr/bin/env bash
set -euo pipefail
ROOT=/workspace/qwen38-flash-next-tp1
MODEL=/models/Qwen3.8-Flash-Next-FP4-Local
IMAGE=eugr/spark-vllm-b12x:latest
while true; do
  count=$(find "$MODEL/tensors" -maxdepth 1 -type f -name 'model-*.safetensors' 2>/dev/null | wc -l)
  downloading=$(find "$MODEL/.cache" -type f -name '*.incomplete' 2>/dev/null | wc -l)
  if [[ "$count" == 21 && "$downloading" == 0 ]] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    break
  fi
  sleep 30
done
RUN=V001-fp4local-vllm-mtp4-20261113 bash "$ROOT/vllm/serve_fp4_local.sh"