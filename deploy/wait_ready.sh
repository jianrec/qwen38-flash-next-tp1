#!/usr/bin/env bash
set -euo pipefail
RUN=${1:?run id required}
ATTEMPTS=${2:-180}
ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
LOG=$ROOT/runs/$RUN/logs/launch.log
RESOURCES=$ROOT/runs/$RUN/results/resources.csv
sleep 3
for ((attempt=1; attempt<=ATTEMPTS; attempt++)); do
  if curl -fsS --max-time 3 http://127.0.0.1:32000/health >/dev/null 2>&1; then
    echo "${RUN}_HEALTH_READY"
    exit 0
  fi
  running=$(docker inspect --format '{{.State.Running}}' "${CONTAINER_PREFIX:-zj2-tp1}-$RUN" 2>/dev/null || true)
  if [[ "$running" != true ]]; then
    echo "${RUN}_CONTAINER_STOPPED"
    tail -n 80 "$LOG" 2>/dev/null || true
    exit 1
  fi
  if [[ -s "$RESOURCES" ]]; then
    read -r available swap_delta < <(awk -F, 'NR==2{base=$3} NR>1 && NF==4{a=$2;d=base-$3} END{print a,d}' "$RESOURCES")
    if (( ${available:-0} < 16777216 || ${swap_delta:-0} > 1048576 )); then
      echo "${RUN}_RESOURCE_FAIL available_kib=$available swap_growth_kib=$swap_delta"
      exit 2
    fi
  fi
  if (( attempt % 30 == 0 )); then
    echo "${RUN}_WAIT attempt=$attempt"
    tr '\r' '\n' < "$LOG" | tail -n 1 | cut -c1-500 || true
  fi
  sleep 10
done
echo "${RUN}_START_TIMEOUT"
exit 3
