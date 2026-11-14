# TP2 复现步骤

## 1. 配置

将 [`configs/best.env`](configs/best.env) 复制到两台机器的实验根目录，分别命名为 `config-E089.env`。两台机器使用同一个 RUN 和不同的本地 CACHE 子目录由配置管理。

实验根目录示例

```bash
cd '/home/admin/zhoujian/qwen3.8flash next'
set -a
. ./config-E089.env
set +a
```

## 2. 启动双 rank

zj2 使用 rank0，zj3 使用 rank1。两台机器分别执行

```bash
./deploy/start_guardian.sh
nohup ./deploy/launch_tp2_local.sh \
  >"logs/$RUN/rank${RANK}.launch.log" 2>&1 < /dev/null &
```

启动后保存实际参数

```bash
docker inspect --format '{{json .Args}}' "$RUN-r$RANK" \
  >"logs/$RUN/server-cli-r${RANK}.json"
```

## 3. Smoke

先发送最小请求，确认 HTTP 200 和答案正确，再开始容量回放。

## 4. 64 文档回放

使用 [`bench/run_l3_trace.sh`](bench/run_l3_trace.sh)。该脚本包含 8 个目标、56 个压力文档、60 秒写回等待、`--require-storage` 和 `--require-full-replay-cache`。

```bash
bash bench/run_l3_trace.sh
```

## 5. 性能重复

```bash
python3 bench/phase1_benchmark.py \
  --url http://127.0.0.1:31000/v1/chat/completions \
  --output results/performance_sequential \
  --container "$RUN-r0" \
  --warmup 2 --measured 8 --prefix-units 400 \
  --pressure-unique 0 --replay-shared 4 \
  --max-new-tokens 128 --timeout 900

python3 bench/concurrent_hot_benchmark.py \
  --url http://127.0.0.1:31000/v1/chat/completions \
  --output results/performance_concurrent \
  --tag tp2-hot --repeats 3 --concurrency 2 \
  --prefix-units 400 --max-new-tokens 64 --timeout 900
```

## 6. 停止

```bash
./deploy/stop.sh
```

不要同步本地 `cache/` 目录。原始结果、日志、资源采样和 server args 已按 RUN 保存在 `results/` 与 `logs/`。
