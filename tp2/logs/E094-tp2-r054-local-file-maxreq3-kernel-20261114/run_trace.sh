#!/usr/bin/env bash
set -eu
cd "/home/admin/zhoujian/qwen3.8flash next"
set -a
source config-E094.env
set +a
out=results/by-run/E094/artifacts/replay64
mkdir -p "$(dirname "$out")"
mkdir "$out"
finish() {
    status=$?
    cp monitor/status.json "$out/guardian-after-zj2.json" || true
    ssh -o BatchMode=yes -o ConnectTimeout=5 10.240.23.2 'cd "/home/admin/zhoujian/qwen3.8flash next" && set -a && source config-E094.env && set +a && cat monitor/status.json' >"$out/guardian-after-zj3.json" || true
    ./deploy/stop.sh || true
    ssh -o BatchMode=yes -o ConnectTimeout=5 10.240.23.2 'cd "/home/admin/zhoujian/qwen3.8flash next" && set -a && source config-E094.env && set +a && ./deploy/stop.sh' || true
    printf '%s\n' "$status" > "$out/exit-code"
}
trap finish EXIT
for i in $(seq 1 120); do
    if grep -q "The server is fired up and ready to roll!" "logs/$RUN/rank0.launch.log" &&
        curl -fsS --max-time 3 http://127.0.0.1:31000/model_info >/dev/null; then
        timeout 20m python3 src/bench/cache_acceptance.py \
          --url http://127.0.0.1:31000/v1/chat/completions \
          --metrics-url http://127.0.0.1:31000/metrics \
          --mooncake-url http://127.0.0.1:1/metrics/summary \
          --run-id "$RUN" --tag E085-L3-SLO --output "$out" --nodes localhost,10.240.23.2 \
          --targets 8 --pressure 56 --prefix-units 400 --settle-seconds 60 \
          --replay-concurrency 8 --require-storage --require-full-replay-cache \
          --min-storage-coverage 0.95 --storage-backend HiCacheFile >"$out/stdout.log" 2>&1
        exit 0
    fi
    if [ "$i" -gt 4 ] && [ -z "$(docker ps -q --filter "name=^/${RUN}-r0$")" ]; then
        echo "E047 rank0 exited before readiness"
        exit 1
    fi
    sleep 5
done
echo "E047 readiness timed out"
exit 124
