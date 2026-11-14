#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

STORE_SERVICE_CONTAINER=${STORE_SERVICE_CONTAINER:-$RUN-mooncake-store-service}
for name in "$CONTAINER" "$STORE_CONTAINER" "$STORE_SERVICE_CONTAINER"; do
  docker rm -f "$name" >/dev/null 2>&1 || true
done
if [ "$NODE_RANK" = "0" ]; then
  docker rm -f "$MASTER_CONTAINER" >/dev/null 2>&1 || true
fi
echo "stopped run=${RUN} rank=${RANK}"

