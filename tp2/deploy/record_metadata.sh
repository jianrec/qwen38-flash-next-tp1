#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

mkdir -p "$LOG_DIR"
CONFIG_SNAPSHOT=$LOG_DIR/config-rank$RANK.env
METADATA_SNAPSHOT=$LOG_DIR/metadata-rank$RANK.txt

NAMES=(
  EXPERIMENT_ID EXPERIMENT_CONTROL EXPERIMENT_CHANGE
  RUN IMAGE MODEL QUANT KV CTX MEMFRAC MEMLIMIT_GIB MAXREQ MAXTOKENS MAMBACACHE MASTER_ENABLE_OFFLOAD
  PAGE_SIZE CHUNKED_PREFILL_SIZE MAMBA_RADIX_STRATEGY MAMBA_SSM_DTYPE MAMBA_MAX_STATES_PER_PATH
  MAMBA_TRACK_INTERVAL DETERMINISTIC RANDOM_SEED
  MOONCAKE_MODE STANDALONE_STORAGE MOONCAKE_PROTOCOL MOONCAKE_METADATA MOONCAKE_DEVICE
  MOONCAKE_DEVICE_RANK0 MOONCAKE_DEVICE_RANK1
  MOONCAKE_GLOBAL_SEGMENT_SIZE HICACHE_SIZE_GIB HICACHE_HOST_MEMORY_MODE
  STORE_SERVICE_SEGMENT_SIZE STORE_SERVICE_LOCAL_BUFFER_SIZE STORE_SERVICE_MEMORY_GIB
  STORE_SERVICE_PORT STORE_SERVICE_SSD_OFFLOAD STORE_SERVICE_NODE_RANK
  HICACHE_WRITE_POLICY HICACHE_PREFETCH HICACHE_IO_BACKEND HICACHE_MEM_LAYOUT
  HICACHE_STORAGE_BACKEND HICACHE_STORAGE_EXTRA_CONFIG
  SSD_OFFLOAD SSD_OFFLOAD_PATH NCCL_IB_DISABLE IFACE MASTER GROUP
  EVICTION_HIGH_WATERMARK EVICTION_RATIO KV_LEASE_TTL KV_SOFT_PIN_TTL GUARDIAN_PORT
)
: > "$CONFIG_SNAPSHOT"
for name in "${NAMES[@]}"; do
  printf '%s=%q\n' "$name" "${!name-}" >> "$CONFIG_SNAPSHOT"
done

{
  printf 'timestamp=%s\n' "$(date -Iseconds)"
  printf 'hostname=%s\n' "$(hostname -s)"
  printf 'rank=%s\n' "$RANK"
  printf 'local_ip=%s\n' "$LOCAL_IP"
  printf 'kernel=%s\n' "$(uname -srmo)"
  printf 'image_id=%s\n' "$(docker image inspect --format '{{.Id}}' "$IMAGE")"
  printf 'sglang_version=%s\n' "$(docker run --rm --entrypoint /opt/sglang/bin/python3 "$IMAGE" -I -c 'import sglang; print(sglang.__version__)' 2>/dev/null)"
  if [ -d /home/admin/mooncake/.git ]; then
    printf 'mooncake_source_commit=%s\n' "$(git -C /home/admin/mooncake rev-parse HEAD)"
  fi
  printf 'model_config_sha256=%s\n' "$(sha256sum "$MODEL/config.json" | cut -d' ' -f1)"
  if [ -f "$MODEL/model.safetensors.index.json" ]; then
    printf 'model_index_sha256=%s\n' "$(sha256sum "$MODEL/model.safetensors.index.json" | cut -d' ' -f1)"
  fi
} > "$METADATA_SNAPSHOT"

sha256sum "$SCRIPT_DIR"/*.sh > "$LOG_DIR/script-sha256-rank$RANK.txt"
if [ -d "$EXP_DIR/zhoujian_storage" ]; then
    find -H "$EXP_DIR/zhoujian_storage" -type f -name '*.py' -print0 | sort -z | xargs -0 -r sha256sum \
    > "$LOG_DIR/custom-storage-sha256-rank$RANK.txt"
fi
cp "$SGLANG_CONFIG" "$LOG_DIR/mooncake-sglang-rank$RANK.json"
{
  free -h
  printf '\n--- /proc/meminfo ---\n'
  cat /proc/meminfo
  printf '\n--- docker stats ---\n'
  docker stats --no-stream
} > "$LOG_DIR/resources-before-rank$RANK.txt"
