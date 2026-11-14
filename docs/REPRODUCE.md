# 复现步骤

## 0. 前置条件

先读 [PREREQUISITES.md](PREREQUISITES.md)。需要：

- 1 × NVIDIA GB10，约 128 GiB UMA
- 同一份 Qwen3.8 Flash Next checkpoint
- 与 checkpoint 匹配的 SGLang 自定义镜像
- 至少 80 GiB 空闲磁盘
- 单实例，最多 2 并发

模型权重、私有镜像、PLE 大表和运行缓存不在本仓库中。

## 1. 设置路径

```bash
export PROJECT_ROOT="$HOME/qwen38-flash-next-tp1"
export MODEL="/models/qwen38-flash-next-serving-bf16mtp-v1"
export IMAGE="<your-sglang-image>"
export ALLOW_HOST="$(hostname -s)"
cd "$PROJECT_ROOT"
```

`ALLOW_HOST` 是可选的保护开关；不设置时脚本不校验主机名。

## 2. 一次性准备 PLE 复用视图

```bash
bash deploy/prepare_ple_reuse.sh
```

脚本会：

- 硬链接 checkpoint 的 245 个主路径分片和 128 个 PLE 分片；
- 新增只含 `weight_scale=0.0002` 的小文件；
- 只在独立视图中更新 index；
- 不修改原始 checkpoint。

准备完成后，`$PROJECT_ROOT/checkpoints/<checkpoint_id>/ple/` 下应只有一份 `ple_table*.bin`，大小 51,200,245,760 bytes。

## 3. 启动 R054

```bash
RUN=R064-fp8kv-mamba-bf16-direct-mf078-$(date +%Y%m%d) \
KV_DTYPE=fp8_e4m3 \
MAMBA_SSM_DTYPE=bfloat16 \
MAX_TOTAL_TOKENS=auto \
CONTEXT_LENGTH=262144 \
MEMFRAC=0.78 \
PLE_REUSE_ONLY=1 \
HICACHE=1 \
HICACHE_STORAGE=file \
HICACHE_SIZE_GIB=4 \
HICACHE_IO_BACKEND=direct \
HICACHE_MEM_LAYOUT=page_first_direct \
PREFILL_GRAPH=0 \
PLE_RSS_BUDGET_GIB=8 \
bash deploy/serve.sh
```

等待服务：

```bash
bash deploy/wait_ready.sh "$RUN"
```

启动日志、配置和资源采样在 `runs/$RUN/`。`config.env` 记录本次实际参数；`resources.csv` 记录每 5 秒的 MemAvailable 和 SwapFree。

## 4. 短请求

```bash
python3 -I deploy/smoke.py "runs/$RUN/results/smoke.json"
```

要求 HTTP 200，答案包含 `2`。

## 5. 冷 8k/256 双并发（三次独立重启）

每次重启后使用新的 RUN 和新的 output 目录：

```bash
python3 -I bench/cold_concurrent.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --output "runs/$RUN/results/cold_concurrent" \
  --round-id "$RUN" \
  --pairs 10 --prefix-units 574 --max-new-tokens 256 --timeout 900
```

三轮完成后聚合：

```bash
python3 -I bench/aggregate_cold_rounds.py \
  --runs runs/R064-cold1 runs/R064-cold2 runs/R064-cold3 \
  --output results/aggregate-cold/aggregate.json
```

## 6. 262k 长上下文

```bash
python3 -I bench/cache_acceptance.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --metrics-url http://127.0.0.1:32000/metrics \
  --mooncake-url http://127.0.0.1:1/metrics \
  --run-id "$RUN" --tag long262k-v1 \
  --output "runs/$RUN/results/long_context" \
  --nodes local --targets 1 --pressure 0 --prefix-units 18700 \
  --max-new-tokens 64 --timeout 900
```

实际输入约 261,925 tokens。

## 7. 480,718-token 压力与回放

```bash
python3 -I bench/cache_acceptance.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --metrics-url http://127.0.0.1:32000/metrics \
  --mooncake-url http://127.0.0.1:1/metrics \
  --run-id "$RUN" --tag auto-pressure-v1 \
  --output "runs/$RUN/results/cache_pressure" \
  --nodes local --targets 4 --pressure 80 --prefix-units 400 \
  --max-new-tokens 64 --timeout 900 \
  --require-full-replay-cache --storage-backend HiCacheFile \
  --settle-seconds 60 --replay-concurrency 2
```

## 8. 单请求与 2 并发性能

```bash
python3 -I bench/phase1_benchmark.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --output "runs/$RUN/results/performance_sequential" \
  --container "zj2-tp1-$RUN" \
  --warmup 2 --measured 8 --prefix-units 400 --prefix-tag tp1-seq-v1 \
  --pressure-unique 0 --replay-shared 4 --max-new-tokens 128 --timeout 900

python3 -I bench/concurrent_hot_benchmark.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --output "runs/$RUN/results/performance_concurrent" \
  --tag tp1-hot-v1 --repeats 3 --concurrency 2 \
  --prefix-units 400 --max-new-tokens 64 --timeout 900
```

## 9. 质量

```bash
python3 -I bench/quality_acceptance.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --mmlu-dir datasets/mmlu/data/test \
  --gsm8k datasets/gsm8k-test.jsonl \
  --output "runs/$RUN/results/quality_mmlu" \
  --mmlu-per-subject 1 --gsm-count 0 --max-mmlu-tokens 8 --timeout 300

python3 -I bench/long_quality.py \
  --url http://127.0.0.1:32000/v1/chat/completions \
  --output "runs/$RUN/results/long_quality" \
  --tag long-quality-v1 --cases 20 --max-new-tokens 64 --timeout 900
```

## 10. 资源与安全

- 同一时间只允许一个 RUN；`serve.sh` 使用 `serial.lock`。
- 启动前要求容器上限之外至少 16 GiB MemAvailable；建议实际留 20–24 GiB。
- 容器限制为 98 GiB；如做新的内存分层实验，先降到 88–90 GiB。
- 运行中监控 `results/resources.csv`；低于 18 GiB 应停止容器。
- 不要在新的内存分层路径上无人值守运行。

## 11. 停止与同步

```bash
docker stop --time 10 "zj2-tp1-$RUN"
```

只同步结果，不同步 `cache/`：

```bash
rsync -a --exclude 'cache/' \
  "$PROJECT_ROOT/runs/$RUN/" \
  "./results/$RUN/"
```

## 12. 可选 vLLM

V008 是另一套 checkpoint + 引擎，不是 R054 的单变量对照。需要 `eugr/spark-vllm-b12x` 和 NVIDIA FP4-Local 权重：

```bash
RUN=V009-fp4local-vllm-nomtp-mem077-conv-bf16-$(date +%Y%m%d) \
MODEL=/models/Qwen3.8-Flash-Next-FP4-Local \
GPU_MEM_UTIL=0.77 MTP=0 \
MAMBA_CACHE_DTYPE=bfloat16 MAMBA_SSM_CACHE_DTYPE=float32 \
bash vllm/serve_fp4_local.sh
```

它适合新请求/近期热请求；压力回放仍明显弱于 R054。