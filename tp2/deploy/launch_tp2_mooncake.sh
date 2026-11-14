#!/usr/bin/env bash
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"
"$SCRIPT_DIR/prepare_config.sh" >/dev/null
"$SCRIPT_DIR/record_metadata.sh"
set -euo pipefail

RUN=${RUN:-fp8run1}
QUANT=${QUANT:-modelopt_fp4} # modelopt_fp4 | fp8 | none
KV=${KV:-auto}               # auto(bf16) | fp8
RANK=${RANK:-$NODE_RANK}
: "${RANK:?set RANK or configure node}"
GROUP=${GROUP:-G23}
WORLD=${WORLD:-2}
MASTER=${MASTER:-10.240.23.1:29680}
IFACE=${IFACE:-enp1s0f0np0}
MODEL=${MODEL:-/home/admin/chy/data/models/qwen38-flash-next-serving-bf16mtp-v1}
DRAFT=${DRAFT:-$MODEL}
if [ -d "$HOME/zhoujian/fp8study" ]; then
  BASE=${BASE:-$HOME/zhoujian/fp8study}
else
  BASE=${BASE:-$HOME/fp8study}
fi
IMAGE=${IMAGE:-flashnext-base:090c06d563692003}
CTX=${CTX:-16384}
MEMFRAC=${MEMFRAC:-0.55}
MEMLIMIT_GIB=${MEMLIMIT_GIB:-100}
MAXREQ=${MAXREQ:-4}
MAXTOKENS=${MAXTOKENS:-65536}
MAMBACACHE=${MAMBACACHE:-20}
PORT=${PORT:-31000}
CONFIG=${CONFIG:-A}
GROUP=${GROUP:?set GROUP to G23 or G45}
PEER_URL=${PEER_URL:-}
if [ -z "$PEER_URL" ]; then
  case "$GROUP:$RANK" in
    G23:0) PEER_URL=http://10.240.23.2:9101/guardian ;;
    G23:1) PEER_URL=http://10.240.23.1:9101/guardian ;;
    G45:0) PEER_URL=http://192.168.201.108:9101/guardian ;;
    G45:1) PEER_URL=http://192.168.201.107:9101/guardian ;;
    *) echo "GROUP must be G23/G45 and RANK must be 0/1"; exit 2 ;;
  esac
fi

CACHE=${CACHE:-$BASE/cache/$RUN/rank$RANK}
STATE=$BASE/monitor
CONTAINER=$RUN-r$RANK
python3 "$BASE/qwen_node_agent.py" preflight --state-dir "$STATE" \
  --container "$CONTAINER" --model "$MODEL" --quant "$QUANT" \
  --memory-limit-gib "$MEMLIMIT_GIB" --context-length "$CTX" --max-total-tokens "$MAXTOKENS"
case "$KV" in auto|fp8) ;; *) echo "KV must be auto or fp8"; exit 2 ;; esac
[[ "$MAXREQ" =~ ^[1-9][0-9]*$ && "$MAMBACACHE" =~ ^[1-9][0-9]*$ ]] || { echo "invalid request/Mamba cache limit"; exit 2; }
[[ "$PAGE_SIZE" =~ ^[1-9][0-9]*$ && "$MAMBA_TRACK_INTERVAL" =~ ^[1-9][0-9]*$ ]] || { echo "invalid page size/Mamba track interval"; exit 2; }
(( MAMBA_TRACK_INTERVAL % PAGE_SIZE == 0 )) || { echo "MAMBA_TRACK_INTERVAL must be a multiple of PAGE_SIZE"; exit 2; }
[[ "$MAMBA_MAX_STATES_PER_PATH" =~ ^-1$|^[1-9][0-9]*$ ]] || { echo "MAMBA_MAX_STATES_PER_PATH must be -1 or a positive integer"; exit 2; }
case "$HICACHE_IO_BACKEND" in direct|kernel) ;; *) echo "HICACHE_IO_BACKEND must be direct or kernel"; exit 2 ;; esac
case "$HICACHE_MEM_LAYOUT" in page_first|page_first_direct) ;; *) echo "HICACHE_MEM_LAYOUT must be page_first or page_first_direct"; exit 2 ;; esac
case "$HICACHE_STORAGE_BACKEND" in mooncake|dynamic|file) ;; *) echo "HICACHE_STORAGE_BACKEND must be mooncake, dynamic, or file"; exit 2 ;; esac
if [ "$HICACHE_STORAGE_BACKEND" = "dynamic" ] && [ -z "$HICACHE_STORAGE_EXTRA_CONFIG" ]; then
  echo "dynamic storage requires HICACHE_STORAGE_EXTRA_CONFIG"; exit 2
fi
case "$MAMBA_RADIX_STRATEGY" in no_buffer|extra_buffer|extra_buffer_lazy) ;; *) echo "invalid MAMBA_RADIX_STRATEGY"; exit 2 ;; esac
case "$MAMBA_SSM_DTYPE" in ""|float32|bfloat16|float16) ;; *) echo "invalid MAMBA_SSM_DTYPE"; exit 2 ;; esac
case "$DETERMINISTIC" in 0|1) ;; *) echo "DETERMINISTIC must be 0 or 1"; exit 2 ;; esac
python3 - "$MEMFRAC" <<'PY'
import sys
fraction = float(sys.argv[1])
if not 0 < fraction < 1:
    raise SystemExit("MEMFRAC must be between 0 and 1")
PY
mkdir -p "$CACHE" "$BASE/logs"

ARGS=(-m sglang.launch_server
  --model-path /model --served-model-name qwen38-flash-next
  --weight-loader-disable-mmap
  --trust-remote-code --host 0.0.0.0 --port "$PORT"
  --tp-size "$WORLD" --pp-size 1 --nnodes "$WORLD" --node-rank "$RANK"
  --dist-init-addr "$MASTER"
  --context-length "$CTX"
  --mem-fraction-static "$MEMFRAC"
  --max-running-requests "$MAXREQ"
  --max-total-tokens "$MAXTOKENS"
  --max-mamba-cache-size "$MAMBACACHE"
  --mamba-max-states-per-path "$MAMBA_MAX_STATES_PER_PATH"
  --mamba-track-interval "$MAMBA_TRACK_INTERVAL"
  --chunked-prefill-size "$CHUNKED_PREFILL_SIZE"
  --page-size "$PAGE_SIZE"
  --ple-offload-embedding --ple-offload-backend file --ple-offload-dir /cache/ple
  --mamba-radix-cache-strategy "$MAMBA_RADIX_STRATEGY"
  --attention-backend flashinfer
  --enable-metrics --enable-cache-report
  --random-seed "$RANDOM_SEED")

if [ -n "$MAMBA_SSM_DTYPE" ]; then
  ARGS+=(--mamba-ssm-dtype "$MAMBA_SSM_DTYPE")
fi
if [ "$DETERMINISTIC" = "1" ]; then
  ARGS+=(--enable-deterministic-inference)
fi

if [ "$MOONCAKE_MODE" = "1" ]; then
  ARGS+=(--enable-hierarchical-cache
    --hicache-host-memory-mode "$HICACHE_HOST_MEMORY_MODE"
    --hicache-size "$HICACHE_SIZE_GIB"
    --hicache-io-backend "$HICACHE_IO_BACKEND"
    --hicache-mem-layout "$HICACHE_MEM_LAYOUT"
    --hicache-write-policy "$HICACHE_WRITE_POLICY"
    --hicache-storage-backend "$HICACHE_STORAGE_BACKEND"
    --hicache-storage-prefetch-policy "$HICACHE_PREFETCH")
  if [ -n "$HICACHE_STORAGE_EXTRA_CONFIG" ]; then
    ARGS+=(--hicache-storage-backend-extra-config "$HICACHE_STORAGE_EXTRA_CONFIG")
  fi
fi

case "$QUANT" in
  fp8)          ARGS+=(--quantization fp8) ;;
  modelopt_fp4) ARGS+=(--quantization modelopt_fp4 --fp4-gemm-backend flashinfer_cutlass) ;;
  none)         ;;
  *) echo "bad QUANT"; exit 2 ;;
esac
if [ "$KV" = "fp8" ]; then ARGS+=(--kv-cache-dtype fp8_e4m3); fi

# Do not replace a rank until this node's independent guardian is reachable.
python3 "$BASE/qwen_node_agent.py" status --state-dir "$STATE" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
ARM=(--state-dir "$STATE" --container "$CONTAINER" --run-id "$RUN" --config "$CONFIG" --group "$GROUP")
if [ -n "$PEER_URL" ]; then ARM+=(--peer-url "$PEER_URL"); fi
python3 "$BASE/qwen_node_agent.py" arm "${ARM[@]}"
CIDFILE=$STATE/$CONTAINER.cid
rm -f "$CIDFILE"
cleanup() {
  local container_id
  if [ -s "$CIDFILE" ]; then
    container_id=$(cat "$CIDFILE")
    if [[ "$container_id" =~ ^[a-f0-9]{64}$ ]] && [ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" = true ]; then
      timeout 12 docker stop --time 5 "$container_id" >/dev/null 2>&1 || true
      if [ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" = true ]; then
        timeout 5 docker kill "$container_id" >/dev/null 2>&1 || true
      fi
      if [ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" = true ]; then
        echo "container still running; guardian remains armed" >&2
        return 1
      fi
    fi
  fi
  python3 "$BASE/qwen_node_agent.py" disarm --state-dir "$STATE" --run-id "$RUN" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

DOCKER_EXTRA=()
if [ "$HICACHE_STORAGE_BACKEND" = "dynamic" ]; then
  [ -d "$EXP_DIR/zhoujian_storage" ] || { echo "missing $EXP_DIR/zhoujian_storage"; exit 2; }
  DOCKER_EXTRA+=(
    -v "$EXP_DIR/zhoujian_storage:/opt/zhoujian_storage:ro"
    -e PYTHONPATH=/opt
  )
fi

docker run --rm --name "$CONTAINER" --cidfile "$CIDFILE" \
  --memory "${MEMLIMIT_GIB}g" --memory-swap "${MEMLIMIT_GIB}g" \
  --network host --ipc host --gpus all \
  --cap-add IPC_LOCK --ulimit memlock=-1 --ulimit stack=67108864 \
  --device /dev/infiniband \
  -e NCCL_DEBUG=WARN \
  -e NCCL_IB_DISABLE="$NCCL_IB_DISABLE" \
  -e NCCL_SOCKET_IFNAME="$IFACE" \
  -e GLOO_SOCKET_IFNAME="$IFACE" \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HOME=/cache/huggingface -e XDG_CACHE_HOME=/cache \
  -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor \
  -e FLASHINFER_WORKSPACE_BASE=/cache/flashinfer \
  -e SGLANG_WARMUP_TIMEOUT=3600 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -v "$MODEL:/model:ro" -v "$DRAFT:/draft:ro" -v "$CACHE:/cache" \
  -v "$SGLANG_CONFIG:/etc/mooncake/mooncake_sglang.json:ro" \
  -e SGLANG_HICACHE_MOONCAKE_CONFIG_PATH=/etc/mooncake/mooncake_sglang.json \
  -e MOONCAKE_STANDALONE_STORAGE="$STANDALONE_STORAGE" \
  -e MOONCAKE_CLIENT=${LOCAL_IP}:${STORE_PORT} \
  -e PYTHONHASHSEED=0 \
  "${DOCKER_EXTRA[@]}" \
  --entrypoint python3 "$IMAGE" "${ARGS[@]}"
