#!/usr/bin/env bash
set -euo pipefail

# Local HiCache + local SSD entrypoint. The shared launcher contains the
# guardian, TP2, metrics, cleanup, and metadata logic. This wrapper selects
# SGLang's built-in file backend and never starts Mooncake services.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export MOONCAKE_MODE=1
export HICACHE_STORAGE_BACKEND=file
export HICACHE_STORAGE_EXTRA_CONFIG=${HICACHE_STORAGE_EXTRA_CONFIG:-'{"file_storage_path":"/cache/hicache-ssd","enable_metadata_cache":true}'}
export SSD_OFFLOAD=0
exec "$SCRIPT_DIR/launch_tp2_mooncake.sh" "$@"
