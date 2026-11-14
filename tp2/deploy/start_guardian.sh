#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

GUARDIAN_LOG_DIR=${GUARDIAN_LOG_DIR:-$BASE/logs/$RUN}
GUARDIAN_LOG=$GUARDIAN_LOG_DIR/guardian-${GUARDIAN_PORT}.log
GUARDIAN_PID=$GUARDIAN_LOG_DIR/guardian-${GUARDIAN_PORT}.pid
mkdir -p "$GUARDIAN_LOG_DIR" "$CONFIG_DIR"

if curl -fsS --max-time 2 "http://127.0.0.1:${GUARDIAN_PORT}/healthz" >/dev/null 2>&1; then
  echo "guardian already ready on port ${GUARDIAN_PORT}"
  exit 0
fi

nohup python3 "$BASE/qwen_node_agent.py" serve \
  --state-dir "$BASE/monitor" --port "$GUARDIAN_PORT" \
  >"$GUARDIAN_LOG" 2>&1 < /dev/null &
echo $! > "$GUARDIAN_PID"

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${GUARDIAN_PORT}/healthz" >/dev/null 2>&1; then
    echo "guardian ready on port ${GUARDIAN_PORT}"
    exit 0
  fi
  sleep 1
done

echo "guardian failed to become ready on port ${GUARDIAN_PORT}" >&2
exit 1
