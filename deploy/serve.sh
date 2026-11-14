#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN=${RUN:?set RUN}
KV_DTYPE=${KV_DTYPE:?set KV_DTYPE to auto, fp8_e4m3, nvfp4, or fp4_mx_block16}
MODEL=${MODEL:?set MODEL to the checkpoint directory}
IMAGE=${IMAGE:?set IMAGE to the SGLang container image}
PORT=32000
MEMLIMIT_GIB=98
MEMFRAC=${MEMFRAC:-0.78}
MAX_TOTAL_TOKENS=${MAX_TOTAL_TOKENS:-65536}
PLE_REUSE_ONLY=${PLE_REUSE_ONLY:-0}
HICACHE=${HICACHE:-0}
HICACHE_SIZE_GIB=${HICACHE_SIZE_GIB:-3}
HICACHE_STORAGE=${HICACHE_STORAGE:-file}
HICACHE_IO_BACKEND=${HICACHE_IO_BACKEND:-kernel}
HICACHE_MEM_LAYOUT=${HICACHE_MEM_LAYOUT:-page_first}
PREFILL_GRAPH=${PREFILL_GRAPH:-0}
PLE_RSS_BUDGET_GIB=${PLE_RSS_BUDGET_GIB:-8}
QSA_INDEXER_DTYPE=${QSA_INDEXER_DTYPE:-auto}
MAMBA_SSM_DTYPE=${MAMBA_SSM_DTYPE:-}
MAMBA_RADIX_STRATEGY=${MAMBA_RADIX_STRATEGY:-extra_buffer}
MAMBA_TRACK_INTERVAL=${MAMBA_TRACK_INTERVAL:-256}
LINEAR_ATTN_BACKEND=${LINEAR_ATTN_BACKEND:-triton}
LINEAR_ATTN_DECODE_BACKEND=${LINEAR_ATTN_DECODE_BACKEND:-}
LINEAR_ATTN_PREFILL_BACKEND=${LINEAR_ATTN_PREFILL_BACKEND:-}
PAGE_MAJOR_KV_LAYOUT=${PAGE_MAJOR_KV_LAYOUT:-0}
SPECULATIVE_ALGORITHM=${SPECULATIVE_ALGORITHM:-none}
SPECULATIVE_NUM_STEPS=${SPECULATIVE_NUM_STEPS:-}
SPECULATIVE_EAGLE_TOPK=${SPECULATIVE_EAGLE_TOPK:-}
SPECULATIVE_NUM_DRAFT_TOKENS=${SPECULATIVE_NUM_DRAFT_TOKENS:-}
SPECULATIVE_DRAFT_KV_CACHE_DTYPE=${SPECULATIVE_DRAFT_KV_CACHE_DTYPE:-}
ENABLE_INT8_MAMBA_CHECKPOINT=${ENABLE_INT8_MAMBA_CHECKPOINT:-0}
INT8_MAMBA_CKPT_SIZE=${INT8_MAMBA_CKPT_SIZE:-}
MAX_RUNNING_REQUESTS=2
CONTEXT_LENGTH=${CONTEXT_LENGTH:-16384}
CHUNKED_PREFILL_SIZE=${CHUNKED_PREFILL_SIZE:-4096}
NUM_CONTINUOUS_DECODE_STEPS=${NUM_CONTINUOUS_DECODE_STEPS:-1}

[[ -z "${ALLOW_HOST:-}" || "$(hostname -s)" == "$ALLOW_HOST" ]] || { echo "host must be $ALLOW_HOST" >&2; exit 2; }
[[ "$RUN" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid RUN" >&2; exit 2; }
[[ "$KV_DTYPE" == auto || "$KV_DTYPE" == fp8_e4m3 || "$KV_DTYPE" == nvfp4 || "$KV_DTYPE" == fp4_mx_block16 ]] || { echo "invalid KV_DTYPE" >&2; exit 2; }
[[ "$MAX_TOTAL_TOKENS" == auto || "$MAX_TOTAL_TOKENS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MAX_TOTAL_TOKENS" >&2; exit 2; }
[[ "$PLE_REUSE_ONLY" == 0 || "$PLE_REUSE_ONLY" == 1 ]] || { echo "invalid PLE_REUSE_ONLY" >&2; exit 2; }
[[ "$HICACHE" == 0 || "$HICACHE" == 1 ]] || { echo "invalid HICACHE" >&2; exit 2; }
[[ "$HICACHE_STORAGE" == file || "$HICACHE_STORAGE" == none ]] || { echo "invalid HICACHE_STORAGE" >&2; exit 2; }
[[ "$HICACHE_IO_BACKEND" == kernel || "$HICACHE_IO_BACKEND" == direct ]] || { echo "invalid HICACHE_IO_BACKEND" >&2; exit 2; }
[[ "$HICACHE_MEM_LAYOUT" == page_first || "$HICACHE_MEM_LAYOUT" == page_first_direct ]] || { echo "invalid HICACHE_MEM_LAYOUT" >&2; exit 2; }
[[ "$HICACHE_SIZE_GIB" =~ ^[1-9][0-9]*$ ]] || { echo "invalid HICACHE_SIZE_GIB" >&2; exit 2; }
[[ "$PREFILL_GRAPH" == 0 || "$PREFILL_GRAPH" == 1 ]] || { echo "invalid PREFILL_GRAPH" >&2; exit 2; }
[[ "$PLE_RSS_BUDGET_GIB" =~ ^[1-9][0-9]*$ ]] || { echo "invalid PLE_RSS_BUDGET_GIB" >&2; exit 2; }
[[ "$MEMFRAC" =~ ^0\.[0-9]+$ ]] || { echo "invalid MEMFRAC" >&2; exit 2; }
[[ "$QSA_INDEXER_DTYPE" == auto || "$QSA_INDEXER_DTYPE" == bfloat16 || "$QSA_INDEXER_DTYPE" == fp8_e4m3 ]] || { echo "invalid QSA_INDEXER_DTYPE" >&2; exit 2; }
[[ -z "$MAMBA_SSM_DTYPE" || "$MAMBA_SSM_DTYPE" == float32 || "$MAMBA_SSM_DTYPE" == bfloat16 || "$MAMBA_SSM_DTYPE" == float16 ]] || { echo "invalid MAMBA_SSM_DTYPE" >&2; exit 2; }
[[ "$MAMBA_RADIX_STRATEGY" == no_buffer || "$MAMBA_RADIX_STRATEGY" == extra_buffer || "$MAMBA_RADIX_STRATEGY" == extra_buffer_lazy ]] || { echo "invalid MAMBA_RADIX_STRATEGY" >&2; exit 2; }
[[ "$MAMBA_TRACK_INTERVAL" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MAMBA_TRACK_INTERVAL" >&2; exit 2; }
(( MAMBA_TRACK_INTERVAL % 64 == 0 )) || { echo "MAMBA_TRACK_INTERVAL must be a multiple of 64" >&2; exit 2; }
[[ "$LINEAR_ATTN_BACKEND" =~ ^[a-z_]+$ ]] || { echo "invalid LINEAR_ATTN_BACKEND" >&2; exit 2; }
[[ -z "$LINEAR_ATTN_DECODE_BACKEND" || "$LINEAR_ATTN_DECODE_BACKEND" =~ ^[a-z_]+$ ]] || { echo "invalid LINEAR_ATTN_DECODE_BACKEND" >&2; exit 2; }
[[ -z "$LINEAR_ATTN_PREFILL_BACKEND" || "$LINEAR_ATTN_PREFILL_BACKEND" =~ ^[a-z_]+$ ]] || { echo "invalid LINEAR_ATTN_PREFILL_BACKEND" >&2; exit 2; }
[[ "$PAGE_MAJOR_KV_LAYOUT" == 0 || "$PAGE_MAJOR_KV_LAYOUT" == 1 ]] || { echo "invalid PAGE_MAJOR_KV_LAYOUT" >&2; exit 2; }
[[ "$SPECULATIVE_ALGORITHM" == none || "$SPECULATIVE_ALGORITHM" =~ ^[A-Za-z0-9_]+$ ]] || { echo "invalid SPECULATIVE_ALGORITHM" >&2; exit 2; }
[[ -z "$SPECULATIVE_NUM_STEPS" || "$SPECULATIVE_NUM_STEPS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid SPECULATIVE_NUM_STEPS" >&2; exit 2; }
[[ -z "$SPECULATIVE_EAGLE_TOPK" || "$SPECULATIVE_EAGLE_TOPK" =~ ^[1-9][0-9]*$ ]] || { echo "invalid SPECULATIVE_EAGLE_TOPK" >&2; exit 2; }
[[ -z "$SPECULATIVE_NUM_DRAFT_TOKENS" || "$SPECULATIVE_NUM_DRAFT_TOKENS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid SPECULATIVE_NUM_DRAFT_TOKENS" >&2; exit 2; }
[[ -z "$SPECULATIVE_DRAFT_KV_CACHE_DTYPE" || "$SPECULATIVE_DRAFT_KV_CACHE_DTYPE" =~ ^[a-z0-9_]+$ ]] || { echo "invalid SPECULATIVE_DRAFT_KV_CACHE_DTYPE" >&2; exit 2; }
[[ "$ENABLE_INT8_MAMBA_CHECKPOINT" == 0 || "$ENABLE_INT8_MAMBA_CHECKPOINT" == 1 ]] || { echo "invalid ENABLE_INT8_MAMBA_CHECKPOINT" >&2; exit 2; }
[[ -z "$INT8_MAMBA_CKPT_SIZE" || "$INT8_MAMBA_CKPT_SIZE" =~ ^[1-9][0-9]*$ ]] || { echo "invalid INT8_MAMBA_CKPT_SIZE" >&2; exit 2; }
[[ "$CONTEXT_LENGTH" =~ ^[1-9][0-9]*$ ]] || { echo "invalid CONTEXT_LENGTH" >&2; exit 2; }
[[ "$CHUNKED_PREFILL_SIZE" =~ ^[1-9][0-9]*$ ]] || { echo "invalid CHUNKED_PREFILL_SIZE" >&2; exit 2; }
[[ "$NUM_CONTINUOUS_DECODE_STEPS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid NUM_CONTINUOUS_DECODE_STEPS" >&2; exit 2; }
(( CONTEXT_LENGTH <= 262144 )) || { echo "CONTEXT_LENGTH exceeds model native limit" >&2; exit 2; }
[[ -f "$MODEL/config.json" ]] || { echo "checkpoint not found: $MODEL" >&2; exit 2; }

RUN_DIR=$ROOT/runs/$RUN
LOG_DIR=$RUN_DIR/logs
RESULT_DIR=$RUN_DIR/results
[[ ! -e "$RUN_DIR/config.env" ]] || { echo "RUN already exists: $RUN_DIR" >&2; exit 2; }
mkdir -p "$LOG_DIR" "$RESULT_DIR" "$RUN_DIR/cache"
exec > >(tee -a "$LOG_DIR/launch.log") 2>&1

CHECKPOINT_PATH=$(readlink -f "$MODEL")
CHECKPOINT_ID=$(basename "$CHECKPOINT_PATH")
[[ "$CHECKPOINT_ID" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid checkpoint id" >&2; exit 2; }
PLE_DIR=$ROOT/checkpoints/$CHECKPOINT_ID/ple
mkdir -p "$PLE_DIR"
PLE_REUSE_PATCH=$ROOT/deploy/patches/qwen4_exp.ple_reuse.py
MODEL_LOAD_PATH=$CHECKPOINT_PATH
if [[ "$PLE_REUSE_ONLY" == 1 ]]; then
  [[ -f "$PLE_REUSE_PATCH" ]] || { echo "missing PLE reuse patch" >&2; exit 2; }
  MODEL_LOAD_PATH=$ROOT/checkpoints/$CHECKPOINT_ID/model-reuse
  [[ -f "$MODEL_LOAD_PATH/config.json" ]] || { echo "missing prepared reuse view" >&2; exit 2; }
  [[ -f "$MODEL_LOAD_PATH/preserved-ple-scale.safetensors" ]] || { echo "missing PLE scale" >&2; exit 2; }
  mapfile -t PLE_FILES < <(find "$PLE_DIR" -maxdepth 1 -type f -name 'ple_table*.bin' | sort)
  [[ ${#PLE_FILES[@]} == 1 ]] || { echo "PLE reuse requires exactly one table" >&2; exit 2; }
  [[ $(stat -c %s "${PLE_FILES[0]}") == 51200245760 ]] || { echo "unexpected PLE table size" >&2; exit 2; }
fi
CONTAINER=zj2-tp1-$RUN

command -v flock >/dev/null
exec 9>"$ROOT/serial.lock"
flock -n 9 || { echo "another TP1 RUN is active" >&2; exit 3; }
if [[ -n $(docker ps --filter label=zj2-tp1-managed=true -q) ]]; then
  echo "another managed TP1 container is active" >&2
  exit 3
fi
if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
  echo "container name already exists: $CONTAINER" >&2
  exit 3
fi
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "port $PORT already serves HTTP" >&2
  exit 3
fi

read -r AVAILABLE_KIB SWAP_FREE_KIB < <(awk '/^MemAvailable:/{a=$2}/^SwapFree:/{s=$2}END{print a,s}' /proc/meminfo)
REQUIRED_KIB=$(( (MEMLIMIT_GIB + 16) * 1024 * 1024 ))
(( AVAILABLE_KIB >= REQUIRED_KIB )) || { echo "need ${MEMLIMIT_GIB} GiB container + 16 GiB host reserve" >&2; exit 4; }
FREE_KIB=$(df -k --output=avail "$ROOT" | awk 'NR==2{print $1}')
(( FREE_KIB >= 80 * 1024 * 1024 )) || { echo "need 80 GiB free disk" >&2; exit 4; }

printf 'checkpoint_path=%q\nmodel_load_path=%q\ncheckpoint_id=%q\nple_dir=%q\nple_reuse_only=%q\nhicache=%q\nhicache_size_gib=%q\nhicache_storage=%q\nhicache_io_backend=%q\nhicache_mem_layout=%q\nprefill_graph=%q\nple_rss_budget_gib=%q\nqsa_indexer_dtype=%q\nmamba_ssm_dtype=%q\nmamba_radix_strategy=%q\nmamba_track_interval=%q\nlinear_attn_backend=%q\nlinear_attn_decode_backend=%q\nlinear_attn_prefill_backend=%q\npage_major_kv_layout=%q\nspeculative_algorithm=%q\nspeculative_num_steps=%q\nspeculative_eagle_topk=%q\nspeculative_num_draft_tokens=%q\nspeculative_draft_kv_cache_dtype=%q\nenable_int8_mamba_checkpoint=%q\nint8_mamba_ckpt_size=%q\nrun=%q\nkv_dtype=%q\nmemlimit_gib=%q\nmemfrac=%q\nmax_total_tokens=%q\nmax_running_requests=%q\ncontext_length=%q\nchunked_prefill_size=%q\nnum_continuous_decode_steps=%q\nport=%q\nimage=%q\n' \
  "$CHECKPOINT_PATH" "$MODEL_LOAD_PATH" "$CHECKPOINT_ID" "$PLE_DIR" "$PLE_REUSE_ONLY" "$HICACHE" "$HICACHE_SIZE_GIB" "$HICACHE_STORAGE" "$HICACHE_IO_BACKEND" "$HICACHE_MEM_LAYOUT" "$PREFILL_GRAPH" "$PLE_RSS_BUDGET_GIB" "$QSA_INDEXER_DTYPE" "$MAMBA_SSM_DTYPE" "$MAMBA_RADIX_STRATEGY" "$MAMBA_TRACK_INTERVAL" "$LINEAR_ATTN_BACKEND" "$LINEAR_ATTN_DECODE_BACKEND" "$LINEAR_ATTN_PREFILL_BACKEND" "$PAGE_MAJOR_KV_LAYOUT" "$SPECULATIVE_ALGORITHM" "$SPECULATIVE_NUM_STEPS" "$SPECULATIVE_EAGLE_TOPK" "$SPECULATIVE_NUM_DRAFT_TOKENS" "$SPECULATIVE_DRAFT_KV_CACHE_DTYPE" "$ENABLE_INT8_MAMBA_CHECKPOINT" "$INT8_MAMBA_CKPT_SIZE" "$RUN" "$KV_DTYPE" "$MEMLIMIT_GIB" "$MEMFRAC" \
  "$MAX_TOTAL_TOKENS" "$MAX_RUNNING_REQUESTS" "$CONTEXT_LENGTH" "$CHUNKED_PREFILL_SIZE" "$NUM_CONTINUOUS_DECODE_STEPS" "$PORT" "$IMAGE" > "$RESULT_DIR/config.env"
find "$PLE_DIR" -maxdepth 1 -type f -printf '%f|%s|%i|%T@\n' | sort > "$RESULT_DIR/ple_before.txt"

monitor() {
  printf 'timestamp,mem_available_kib,swap_free_kib,container_running\n' > "$RESULT_DIR/resources.csv"
  while true; do
    read -r available swap_free < <(awk '/^MemAvailable:/{a=$2}/^SwapFree:/{s=$2}END{print a,s}' /proc/meminfo)
    running=$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)
    [[ "$running" == true ]] || running=false
    printf '%s,%s,%s,%s\n' "$(date --iso-8601=seconds)" "$available" "$swap_free" "$running" >> "$RESULT_DIR/resources.csv"
    sleep 5
  done
}
monitor &
MONITOR_PID=$!
cleanup() {
  status=$?
  trap - EXIT
  if [[ $(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true) == true ]]; then
    docker stop --time 10 "$CONTAINER" >/dev/null 2>&1 || true
  fi
  kill "$MONITOR_PID" >/dev/null 2>&1 || true
  wait "$MONITOR_PID" 2>/dev/null || true
  find "$PLE_DIR" -maxdepth 1 -type f -printf '%f|%s|%i|%T@\n' | sort > "$RESULT_DIR/ple_after.txt"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

ARGS=(
  -m sglang.launch_server
  --model-path /model
  --served-model-name qwen38-flash-next
  --trust-remote-code
  --host 0.0.0.0
  --port "$PORT"
  --tp-size 1
  --pp-size 1
  --nnodes 1
  --node-rank 0
  --dist-init-addr 127.0.0.1:29680
  --quantization modelopt_fp4
  --fp4-gemm-backend flashinfer_cutlass
  --moe-runner-backend flashinfer_cutlass
  --kv-cache-dtype "$KV_DTYPE"
  --context-length "$CONTEXT_LENGTH"
  --mem-fraction-static "$MEMFRAC"
  --max-running-requests "$MAX_RUNNING_REQUESTS"
  --num-continuous-decode-steps "$NUM_CONTINUOUS_DECODE_STEPS"
  --max-mamba-cache-size 10
  --chunked-prefill-size "$CHUNKED_PREFILL_SIZE"
  --max-prefill-tokens "$CONTEXT_LENGTH"
  --page-size 64
  --mamba-radix-cache-strategy "$MAMBA_RADIX_STRATEGY"
  --mamba-track-interval "$MAMBA_TRACK_INTERVAL"
  --cuda-graph-max-bs-decode 2
  --attention-backend flashinfer
  --enable-metrics
  --enable-cache-report
  --random-seed 20261103
  --ple-offload-embedding
  --ple-offload-backend file
  --ple-offload-dir /shared/ple
)
if [[ "$PREFILL_GRAPH" == 0 ]]; then
  ARGS+=(--disable-prefill-cuda-graph)
fi
if [[ "$QSA_INDEXER_DTYPE" != auto ]]; then
  ARGS+=(--qsa-indexer-dtype "$QSA_INDEXER_DTYPE")
fi
if [[ -n "$MAMBA_SSM_DTYPE" ]]; then
  ARGS+=(--mamba-ssm-dtype "$MAMBA_SSM_DTYPE")
fi
ARGS+=(--linear-attn-backend "$LINEAR_ATTN_BACKEND")
if [[ -n "$LINEAR_ATTN_DECODE_BACKEND" ]]; then
  ARGS+=(--linear-attn-decode-backend "$LINEAR_ATTN_DECODE_BACKEND")
fi
if [[ -n "$LINEAR_ATTN_PREFILL_BACKEND" ]]; then
  ARGS+=(--linear-attn-prefill-backend "$LINEAR_ATTN_PREFILL_BACKEND")
fi
if [[ "$PAGE_MAJOR_KV_LAYOUT" == 1 ]]; then
  ARGS+=(--enable-page-major-kv-layout)
fi
if [[ "$SPECULATIVE_ALGORITHM" != none ]]; then
  ARGS+=(--speculative-algorithm "$SPECULATIVE_ALGORITHM")
  if [[ -n "$SPECULATIVE_NUM_STEPS" ]]; then
    ARGS+=(--speculative-num-steps "$SPECULATIVE_NUM_STEPS")
  fi
  if [[ -n "$SPECULATIVE_EAGLE_TOPK" ]]; then
    ARGS+=(--speculative-eagle-topk "$SPECULATIVE_EAGLE_TOPK")
  fi
  if [[ -n "$SPECULATIVE_NUM_DRAFT_TOKENS" ]]; then
    ARGS+=(--speculative-num-draft-tokens "$SPECULATIVE_NUM_DRAFT_TOKENS")
  fi
  if [[ -n "$SPECULATIVE_DRAFT_KV_CACHE_DTYPE" ]]; then
    ARGS+=(--speculative-draft-kv-cache-dtype "$SPECULATIVE_DRAFT_KV_CACHE_DTYPE")
  fi
fi
if [[ "$ENABLE_INT8_MAMBA_CHECKPOINT" == 1 ]]; then
  ARGS+=(--enable-int8-mamba-checkpoint)
fi
if [[ -n "$INT8_MAMBA_CKPT_SIZE" ]]; then
  ARGS+=(--int8-mamba-ckpt-size "$INT8_MAMBA_CKPT_SIZE")
fi
if [[ "$MAX_TOTAL_TOKENS" != auto ]]; then
  ARGS+=(--max-total-tokens "$MAX_TOTAL_TOKENS")
fi
if [[ "$HICACHE" == 1 ]]; then
  ARGS+=(
    --enable-hierarchical-cache
    --hicache-host-memory-mode cache
    --hicache-size "$HICACHE_SIZE_GIB"
    --hicache-io-backend "$HICACHE_IO_BACKEND"
    --hicache-mem-layout "$HICACHE_MEM_LAYOUT"
    --hicache-write-policy write_back
  )
  if [[ "$HICACHE_STORAGE" == file ]]; then
    ARGS+=(
      --hicache-storage-backend file
      --hicache-storage-prefetch-policy wait_complete
      --hicache-storage-backend-extra-config '{"file_storage_path":"/cache/hicache-ssd","enable_metadata_cache":false,"max_size":"64Gi","min_free_space":"80Gi"}'
    )
  fi
fi
DOCKER_EXTRA=()
if [[ "$PLE_REUSE_ONLY" == 1 ]]; then
  DOCKER_EXTRA+=(-v "$PLE_REUSE_PATCH:/sgl-workspace/sglang/python/sglang/srt/models/qwen4_exp.py:ro")
fi

printf '%q ' docker run --rm --name "$CONTAINER" --memory "${MEMLIMIT_GIB}g" --memory-swap "${MEMLIMIT_GIB}g" \
  --network host --ipc host --gpus all -v "$MODEL_LOAD_PATH:/model:ro" -v "$RUN_DIR/cache:/cache" \
  -v "$PLE_DIR:/shared/ple" "${DOCKER_EXTRA[@]}" --entrypoint python3 "$IMAGE" "${ARGS[@]}" > "$RESULT_DIR/docker_command.txt"
printf '\n' >> "$RESULT_DIR/docker_command.txt"

echo "[$(date --iso-8601=seconds)] starting RUN=$RUN KV=$KV_DTYPE PLE=$PLE_DIR"
docker run --rm --name "$CONTAINER" --label zj2-tp1-managed=true \
  --memory "${MEMLIMIT_GIB}g" --memory-swap "${MEMLIMIT_GIB}g" \
  --network host --ipc host --gpus all \
  --cap-add IPC_LOCK --ulimit memlock=-1 --ulimit stack=67108864 \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HOME=/cache/huggingface -e XDG_CACHE_HOME=/cache \
  -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor \
  -e FLASHINFER_WORKSPACE_BASE=/cache/flashinfer \
  -e SGLANG_QWEN4_PLE_FILE_RSS_BUDGET_GB="$PLE_RSS_BUDGET_GIB" \
  -e SGLANG_WARMUP_TIMEOUT=3600 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -e PYTHONHASHSEED=0 \
  -v "$MODEL_LOAD_PATH:/model:ro" \
  -v "$RUN_DIR/cache:/cache" \
  -v "$PLE_DIR:/shared/ple" \
  "${DOCKER_EXTRA[@]}" \
  --entrypoint python3 "$IMAGE" "${ARGS[@]}"
