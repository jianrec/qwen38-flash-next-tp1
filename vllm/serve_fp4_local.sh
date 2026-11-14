#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN=${RUN:?set RUN}
MODEL=${MODEL:?set MODEL to the FP4-Local checkpoint directory}
IMAGE=${IMAGE:-eugr/spark-vllm-b12x:latest}
PORT=32000
MEMLIMIT_GIB=98
GPU_MEM_UTIL=${GPU_MEM_UTIL:-0.8}
MAX_NUM_SEQS=${MAX_NUM_SEQS:-2}
MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
MTP=${MTP:-1}
MTP_TOKENS=${MTP_TOKENS:-4}
KV_OFFLOADING_SIZE_GIB=${KV_OFFLOADING_SIZE_GIB:-}
KV_OFFLOADING_BACKEND=${KV_OFFLOADING_BACKEND:-native}
RECURRENT_CHECKPOINT_POLICY=${RECURRENT_CHECKPOINT_POLICY:-auto}
MAMBA_CACHE_DTYPE=${MAMBA_CACHE_DTYPE:-auto}
MAMBA_SSM_CACHE_DTYPE=${MAMBA_SSM_CACHE_DTYPE:-auto}
PREFIX_CACHE_RETENTION_INTERVAL=${PREFIX_CACHE_RETENTION_INTERVAL:-0}
KV_OFFLOAD_FS=${KV_OFFLOAD_FS:-0}
KV_OFFLOAD_FS_THREADS=${KV_OFFLOAD_FS_THREADS:-16}

[[ -z "${ALLOW_HOST:-}" || "$(hostname -s)" == "$ALLOW_HOST" ]] || { echo "host must be $ALLOW_HOST" >&2; exit 2; }
[[ "$RUN" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid RUN" >&2; exit 2; }
[[ "$MTP" == 0 || "$MTP" == 1 ]] || { echo "invalid MTP" >&2; exit 2; }
[[ "$MAX_NUM_SEQS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MAX_NUM_SEQS" >&2; exit 2; }
[[ "$MAX_NUM_BATCHED_TOKENS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MAX_NUM_BATCHED_TOKENS" >&2; exit 2; }
[[ "$MTP_TOKENS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid MTP_TOKENS" >&2; exit 2; }
[[ -z "$KV_OFFLOADING_SIZE_GIB" || "$KV_OFFLOADING_SIZE_GIB" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "invalid KV_OFFLOADING_SIZE_GIB" >&2; exit 2; }
[[ "$KV_OFFLOADING_BACKEND" == native || "$KV_OFFLOADING_BACKEND" == lmcache ]] || { echo "invalid KV_OFFLOADING_BACKEND" >&2; exit 2; }
[[ "$RECURRENT_CHECKPOINT_POLICY" == auto || "$RECURRENT_CHECKPOINT_POLICY" == aligned || "$RECURRENT_CHECKPOINT_POLICY" == request_boundaries ]] || { echo "invalid RECURRENT_CHECKPOINT_POLICY" >&2; exit 2; }
[[ "$MAMBA_CACHE_DTYPE" == auto || "$MAMBA_CACHE_DTYPE" == float32 || "$MAMBA_CACHE_DTYPE" == float16 || "$MAMBA_CACHE_DTYPE" == bfloat16 ]] || { echo "invalid MAMBA_CACHE_DTYPE" >&2; exit 2; }
[[ "$MAMBA_SSM_CACHE_DTYPE" == auto || "$MAMBA_SSM_CACHE_DTYPE" == float32 || "$MAMBA_SSM_CACHE_DTYPE" == float16 || "$MAMBA_SSM_CACHE_DTYPE" == bfloat16 ]] || { echo "invalid MAMBA_SSM_CACHE_DTYPE" >&2; exit 2; }
[[ "$PREFIX_CACHE_RETENTION_INTERVAL" =~ ^[0-9]+$ ]] || { echo "invalid PREFIX_CACHE_RETENTION_INTERVAL" >&2; exit 2; }
[[ "$KV_OFFLOAD_FS" == 0 || "$KV_OFFLOAD_FS" == 1 ]] || { echo "invalid KV_OFFLOAD_FS" >&2; exit 2; }
[[ "$KV_OFFLOAD_FS_THREADS" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KV_OFFLOAD_FS_THREADS" >&2; exit 2; }
[[ -f "$MODEL/config.json" ]] || { echo "model not ready: $MODEL" >&2; exit 4; }
[[ -f "$MODEL/tensors/model-00021-of-00021.safetensors" ]] || { echo "model shards incomplete" >&2; exit 4; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "image not ready: $IMAGE" >&2; exit 4; }

RUN_DIR=$ROOT/vllm-runs/$RUN
LOG_DIR=$RUN_DIR/logs
RESULT_DIR=$RUN_DIR/results
[[ ! -e "$RUN_DIR/config.env" ]] || { echo "RUN already exists: $RUN_DIR" >&2; exit 2; }
mkdir -p "$LOG_DIR" "$RESULT_DIR" "$RUN_DIR/cache"
exec > >(tee -a "$LOG_DIR/launch.log") 2>&1

CONTAINER=zj2-vllm-$RUN
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

printf 'run=%q\nmodel=%q\nimage=%q\nport=%q\ngpu_mem_util=%q\nmax_num_seqs=%q\nmax_num_batched_tokens=%q\nmtp=%q\nmtp_tokens=%q\nkv_offloading_size_gib=%q\nkv_offloading_backend=%q\nrecurrent_checkpoint_policy=%q\nmamba_cache_dtype=%q\nmamba_ssm_cache_dtype=%q\nprefix_cache_retention_interval=%q\nkv_offload_fs=%q\nkv_offload_fs_threads=%q\nmemlimit_gib=%q\n' \
  "$RUN" "$MODEL" "$IMAGE" "$PORT" "$GPU_MEM_UTIL" "$MAX_NUM_SEQS" "$MAX_NUM_BATCHED_TOKENS" "$MTP" "$MTP_TOKENS" "$KV_OFFLOADING_SIZE_GIB" "$KV_OFFLOADING_BACKEND" "$RECURRENT_CHECKPOINT_POLICY" "$MAMBA_CACHE_DTYPE" "$MAMBA_SSM_CACHE_DTYPE" "$PREFIX_CACHE_RETENTION_INTERVAL" "$KV_OFFLOAD_FS" "$KV_OFFLOAD_FS_THREADS" "$MEMLIMIT_GIB" > "$RESULT_DIR/config.env"

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
  exit "$status"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

ARGS=(
  serve /model
  --served-model-name qwen38-flash-next
  --host 0.0.0.0
  --port "$PORT"
  --tensor-parallel-size 1
  --pipeline-parallel-size 1
  --mamba-cache-mode align
  --mamba-cache-dtype "$MAMBA_CACHE_DTYPE"
  --mamba-ssm-cache-dtype "$MAMBA_SSM_CACHE_DTYPE"
  --enable-prefix-caching
  --enable-chunked-prefill
  --dtype bfloat16
  --kv-cache-dtype fp8
  --quantization modelopt_mixed
  --block-size 16
  --load-format b12x
  --max-model-len 262144
  --max-num-seqs "$MAX_NUM_SEQS"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --gdn-decode-kernel b12x
  --linear-backend b12x
  --moe-backend b12x
  --no-enable-flashinfer-autotune
  --mm-encoder-tp-mode data
  --reasoning-parser qwen3
  --tool-call-parser qwen3_xml
  --enable-auto-tool-choice
  --compilation-config '{"pass_config":{"fuse_act_quant":true}}'
  --gpu-memory-utilization "$GPU_MEM_UTIL"
)
if [[ "$KV_OFFLOAD_FS" == 1 ]]; then
  if [[ -z "$KV_OFFLOADING_SIZE_GIB" ]]; then
    KV_OFFLOADING_SIZE_GIB=4
  fi
  CPU_BYTES=$(awk -v size="$KV_OFFLOADING_SIZE_GIB" 'BEGIN{printf "%d", size * 1073741824}')
  ARGS+=(--kv-transfer-config "{\"kv_connector\":\"OffloadingConnector\",\"kv_role\":\"kv_both\",\"kv_connector_extra_config\":{\"spec_name\":\"TieringOffloadingSpec\",\"cpu_bytes_to_use\":$CPU_BYTES,\"blocks_per_chunk\":1,\"eviction_policy\":\"lru\",\"secondary_tiers\":[{\"type\":\"fs\",\"root_dir\":\"/cache/kv-offload\",\"n_read_threads\":$KV_OFFLOAD_FS_THREADS,\"n_write_threads\":$KV_OFFLOAD_FS_THREADS}]}}")
elif [[ -n "$KV_OFFLOADING_SIZE_GIB" ]]; then
  ARGS+=(--kv-offloading-size "$KV_OFFLOADING_SIZE_GIB" --kv-offloading-backend "$KV_OFFLOADING_BACKEND")
fi
if [[ "$RECURRENT_CHECKPOINT_POLICY" != auto ]]; then
  ARGS+=(--recurrent-checkpoint-policy "$RECURRENT_CHECKPOINT_POLICY")
fi
if [[ "$PREFIX_CACHE_RETENTION_INTERVAL" != 0 ]]; then
  ARGS+=(--prefix-cache-retention-interval "$PREFIX_CACHE_RETENTION_INTERVAL")
fi
if [[ "$MTP" == 1 ]]; then
  ARGS+=(--speculative-config "{\"method\":\"mtp\",\"num_speculative_tokens\":$MTP_TOKENS}")
fi

printf '%q ' docker run --rm --name "$CONTAINER" --label zj2-tp1-managed=true \
  --memory "${MEMLIMIT_GIB}g" --memory-swap "${MEMLIMIT_GIB}g" \
  --network host --ipc host --gpus all --shm-size=16g \
  --cap-add IPC_LOCK --ulimit memlock=-1 --ulimit stack=67108864 \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HOME=/cache/huggingface -e XDG_CACHE_HOME=/cache \
  -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton \
  -e FLASHINFER_WORKSPACE_BASE=/cache/flashinfer \
  -e VLLM_PLE_TABLE_MEMORY=disk -e B12X_DISK_BACKEND=io_uring \
  -e CUTE_DSL_ARCH=sm_121a -e SAFETENSORS_FAST_GPU=1 \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn -e VLLM_SSM_CONV_STATE_LAYOUT=DS \
  -e VLLM_USE_AOT_COMPILE=1 -e VLLM_USE_MEGA_AOT_ARTIFACT=1 \
  -e VLLM_USE_V2_MODEL_RUNNER=1 -e B12X_POLICY_MODE=auto \
  -e VLLM_MXFP8_LM_HEAD=1 -e VLLM_B12X_MOE_FP4_LAYER_MAX_INPUT_SCALE=w13 \
  -e VLLM_B12X_MOE_FP4_FORCE_A16=1 -e B12X_W4A16_A4_PREFILL=1 \
  -v "$MODEL:/model:ro" -v "$RUN_DIR/cache:/cache" \
  --entrypoint /bin/bash "$IMAGE" -lc 'exec vllm "$@"' _ "${ARGS[@]}" > "$RESULT_DIR/docker_command.txt"
printf '\n' >> "$RESULT_DIR/docker_command.txt"

echo "[$(date --iso-8601=seconds)] starting vLLM RUN=$RUN MODEL=$MODEL"
docker run --rm --name "$CONTAINER" --label zj2-tp1-managed=true \
  --memory "${MEMLIMIT_GIB}g" --memory-swap "${MEMLIMIT_GIB}g" \
  --network host --ipc host --gpus all --shm-size=16g \
  --cap-add IPC_LOCK --ulimit memlock=-1 --ulimit stack=67108864 \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HOME=/cache/huggingface -e XDG_CACHE_HOME=/cache \
  -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton \
  -e FLASHINFER_WORKSPACE_BASE=/cache/flashinfer \
  -e VLLM_PLE_TABLE_MEMORY=disk -e B12X_DISK_BACKEND=io_uring \
  -e CUTE_DSL_ARCH=sm_121a -e SAFETENSORS_FAST_GPU=1 \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn -e VLLM_SSM_CONV_STATE_LAYOUT=DS \
  -e VLLM_USE_AOT_COMPILE=1 -e VLLM_USE_MEGA_AOT_ARTIFACT=1 \
  -e VLLM_USE_V2_MODEL_RUNNER=1 -e B12X_POLICY_MODE=auto \
  -e VLLM_MXFP8_LM_HEAD=1 -e VLLM_B12X_MOE_FP4_LAYER_MAX_INPUT_SCALE=w13 \
  -e VLLM_B12X_MOE_FP4_FORCE_A16=1 -e B12X_W4A16_A4_PREFILL=1 \
  -v "$MODEL:/model:ro" -v "$RUN_DIR/cache:/cache" \
  --entrypoint /bin/bash "$IMAGE" -lc 'exec vllm "$@"' _ "${ARGS[@]}"