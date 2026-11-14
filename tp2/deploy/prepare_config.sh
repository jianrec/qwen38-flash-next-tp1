#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

mkdir -p "$CONFIG_DIR" "$LOG_DIR" "$CACHE"
if [ "$HICACHE_STORAGE_BACKEND" = "file" ]; then
  mkdir -p "$CACHE/hicache-ssd"
  cat > "$SGLANG_CONFIG" <<EOF
{
  "backend": "file",
  "file_storage_path": "/cache/hicache-ssd"
}
EOF
elif [ "$STANDALONE_STORAGE" = "1" ]; then
  if [ "$SSD_OFFLOAD" = "1" ]; then
    cat > "$SGLANG_CONFIG" <<EOF
{
  "standalone_storage": true,
  "client_server_address": "${LOCAL_IP}:${STORE_PORT}",
  "enable_ssd_offload": true,
  "ssd_offload_path": "${SSD_OFFLOAD_PATH}"
}
EOF
  else
    cat > "$SGLANG_CONFIG" <<EOF
{
  "standalone_storage": true,
  "client_server_address": "${LOCAL_IP}:${STORE_PORT}"
}
EOF
  fi
else
  if [ "$SSD_OFFLOAD" = "1" ]; then
    cat > "$SGLANG_CONFIG" <<EOF
{
  "standalone_storage": false,
  "local_hostname": "${LOCAL_IP}",
  "metadata_server": "${MOONCAKE_METADATA}",
  "master_server_address": "${MASTER_ADDR}",
  "protocol": "${MOONCAKE_PROTOCOL}",
  "device_name": "${MOONCAKE_DEVICE}",
  "global_segment_size": "${MOONCAKE_GLOBAL_SEGMENT_SIZE}",
  "enable_ssd_offload": true,
  "ssd_offload_path": "${SSD_OFFLOAD_PATH}"
}
EOF
  else
    cat > "$SGLANG_CONFIG" <<EOF
{
  "standalone_storage": false,
  "local_hostname": "${LOCAL_IP}",
  "metadata_server": "${MOONCAKE_METADATA}",
  "master_server_address": "${MASTER_ADDR}",
  "protocol": "${MOONCAKE_PROTOCOL}",
  "device_name": "${MOONCAKE_DEVICE}",
  "global_segment_size": "${MOONCAKE_GLOBAL_SEGMENT_SIZE}"
}
EOF
  fi
fi

cat > "$STORE_CONFIG" <<EOF
{
  "local_hostname": "${LOCAL_IP}",
  "metadata_server": "${MOONCAKE_METADATA}",
  "master_server_address": "${MASTER_ADDR}",
  "protocol": "${MOONCAKE_PROTOCOL}",
  "device_name": "${MOONCAKE_DEVICE}",
  "global_segment_size": "${STORE_SEGMENT_SIZE}",
  "local_buffer_size": "${STORE_LOCAL_BUFFER_SIZE}"
}
EOF

chmod 0644 "$SGLANG_CONFIG" "$STORE_CONFIG"
echo "prepared run=${RUN} rank=${RANK} local_ip=${LOCAL_IP}"

