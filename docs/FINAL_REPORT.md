# Qwen3.8 Flash Next 单机 TP1 最终结论

更新：2026-11-14 12:20（北京时间）

## 最终推荐

**采用 R054：NVFP4 权重、FP8 E4M3 主 KV、Mamba SSM BF16、MEMFRAC 0.78、自动 KV 池、262,144 context、Host 4 GiB + HiCacheFile、direct I/O + page_first_direct、关闭 prefill graph。**

R054 只把 R034 的静态内存比例从 0.76 提到 0.78，计算、精度、缓存层级和 I/O 路径完全不变。它把有效 KV 容量从 698,560 提到三次重启中位 **852,800 tokens（范围 843,328–882,368）**，即 **+154,240（+22.1%）**；速度与 R034 基本持平，8k/32k 长题和 91 条压力输出逐字一致。

- 自动有效容量中位 **852,800 tokens**，比 R034 的 698,560 多 **22.1%**，比 R019 的 607,808 多 **40.3%**。
- KV 日志 K+V 分配中位 **9.76 GiB**（三次重启 9.66–10.10 GiB），Host token 池中位 **288,576**（288,384–289,152）。
- 8k/256 冷请求双并发三次独立重启：中位 **25.735 tok/s**（25.657–25.802），与 R032 的 25.995 基本持平（-1.0%）。
- 常规单请求中位 **17.059 tok/s**，唯一前缀 **13.484 tok/s**；2 并发热请求三轮中位 **31.974 tok/s**，与 R034 的 32.541 基本持平（-1.7%）。
- 原生 262k 冷 TTFT **113.318 秒**（R034 116.318，-2.6%），热请求 0.647 秒，热回放 **0.241 秒**；start/middle/end 全对。
- 91/91 压力请求逐字正确、4/4 完整回放；回放吞吐 **17.251 tok/s**，比 R034 的 18.256 低 5.5%，回放平均 TTFT 从 0.617 增至 0.737 秒。
- 最低 MemAvailable **16.19 GiB**，最大 Swap 增长 **10.44 MiB**，无 OOM。余量比 R034 的 18.729 GiB 少 2.54 GiB，但仍高于 16 GiB 预留线。
- 质量：MMLU **52/57**；8k/32k 长题 **20/20 逐字一致**；压力 91/91 逐字一致。GSM8K 未在 R054 重跑，R033 的 176/200 来自同一权重、KV 精度和计算路径，R054 仅改变池大小。

## 一张结果表

| 配置 | 有效 tokens | 日志 K+V (GiB) | Host tokens | 262k 冷 TTFT | 2 并发吞吐 | 最低 MemAvailable | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| R019 FP8、Mamba FP32、0.76、Host 4 | 607,808 | 7.391 | 261,888 | 121.726 s | 31.113 tok/s | 18.778 GiB | 原长上下文基线 |
| R034 FP8、Mamba BF16、0.76、Host 4 + direct | 698,560 | 8.495 | 284,928 | 116.318 s | 32.541 tok/s | 18.729 GiB | 上一版最佳 |
| **R054 同 R034，MEMFRAC 0.78** | **852,800**（843,328–882,368） | **9.76**（9.66–10.10） | **288,576**（288,384–289,152） | **113.318 s** | **31.974 tok/s** | **16.19 GiB** | **最终推荐：容量 +22.1%，速度基本持平** |
| R050 MTP EAGLE、0.77、Host 4 | 65,152 | 0.74 | — | 不可用 | 48.96 tok/s（热） | 17.39 GiB | 容量不足，不能做 262k |
| R053 MTP EAGLE、0.81、Host 3 | 455,616 | 5.22 | — | 120.382 s | 49.2 tok/s（热） | 16.45 GiB | 冷 8k/256 仅 19.76 tok/s，不采用 |
| R047 page-major KV 布局 | 未形成服务 | — | — | — | — | — | 需 unified memory；Qwen4-Exp 明确不支持 |

说明：R054 的容量是三次独立重启的中位数，单次会因启动时可用内存不同在 843,328–882,368 之间波动，不能把 882,368 当成固定值。R034/R054 的 262k 冷 TTFT 受 PLE 页缓存和磁盘状态影响，±3% 不应全部归因于 MEMFRAC。“2 并发吞吐”列是 8k 热前缀、64 max 的 hot benchmark，取三轮中位数；冷 8k/256 双并发见正文，R054 为 25.735 tok/s。表中“日志 K+V”是启动日志的 K size+V size，单位 GiB；`server_info.internal_states.kvcache` 是另一种框架口径，不与该列混用。

## 收益来源

### FP8 KV 的独立对照

这些是只改 KV 精度、未混 Mamba BF16/direct I/O/MEMFRAC 的直接证据：

- 固定 65,536 tokens：KV 记录从 BF16 的 1.548 降至 FP8 的 0.798 GiB，少 **0.750 GiB（48.45%）**；相同 K/V 空间下可从 65,536 扩到 131,072（+100%）。
- 自动池直接对照（16k context）：R008 BF16 为 **436,544 tokens**，R009 FP8 为 **835,520 tokens**，多 **398,976（+91.39%）**。
- 固定 131,072-token 池、GPU-only、8k/256 双并发三轮：TTFT 中位 **5.645→5.553 秒（-1.62%）**，单请求 decode **17.430→17.514 tok/s（+0.48%）**，实例输出吞吐 **25.660→25.838 tok/s（+0.69%）**。因此 FP8 KV 的速度收益主要是容量，不是大幅加速。
- 同组质量：BF16/FP8 各 **60/60** 正确，8k/32k 长题 **20/20** 且逐字一致。

### 内存与 token

- R054 与 R034 的唯一变量是 `mem_fraction_static 0.76 → 0.78`。权重、KV 精度、Mamba BF16、Host 4 GiB、文件缓存和 direct I/O 均未改变。
- 自动池把额外静态预算继续分给 KV：中位 KV 从 8.495 增至 9.76 GB，token 从 698,560 增至 852,800。
- $T_{pool}=852,800$，但 $T_{single}=262,144$，$T_{avg@2}=262,144$；不能把 852,800 当作单请求上下文。
- Host token 池中位 288,576（R034 为 284,928），Host 内存约 3.55 GB。设备池远大于 Host 池，L2 命中率受限是预期行为，压力回放仍 4/4 完整。

### 速度与吞吐

- 冷 8k/256 双并发：25.735 tok/s（R032 25.995，-1.0%），单请求 decode 17.716 tok/s，TTFT 中位 5.695 秒。
- 常规单请求：shared-prefix 17.059 tok/s（R034 17.244，-1.1%），unique-prefix 13.484（-0.4%）。
- 2 并发热请求：三轮中位 31.974 tok/s（R034 32.541，-1.7%），在测量波动内。
- 262k 冷 TTFT 113.318 秒（-2.6%），热回放 0.241 秒（+6.6%）；长上下文收益仍来自避免重新 prefill。
- 压力回放吞吐 17.251 tok/s（-5.5%），回放平均 TTFT 0.737 秒（+19.4%）；这是容量增大后目标前缀更多落在 Host/文件层的代价，仍远低于重算成本。

### 质量与稳定性

- MMLU 52/57，与 R034/R033 相同；20 条 8k/32k 长题全部正确且与 R032 逐字一致；91 条压力请求全部逐字一致。
- 三次冷启动均 20/20 正确、全部 `cached_tokens=0`，吞吐 25.657–25.802 tok/s，说明结果可重复。
- 最低 MemAvailable 16.19 GiB，最大 Swap 增长 10.44 MiB，无 OOM。R054 的内存余量比 R034 薄，长时间运行应继续监控 MemAvailable，不能低于 16 GiB。

## 负结果：MTP、page-major 与旧候选

- **MTP/EAGLE**：checkpoint 自带 NEXTN draft，SGLang 对该架构有专门 `Qwen4ExpForCausalLMMTP`。R050/R051/R053 证明投机解码输出精确、热短输出 2 并发可达 49 tok/s（+49%），但 256-token 冷输出接受长度只有 1.16（接受率 5.4%），冷 8k/256 降到 19.76 tok/s（-23%）；262k 冷 TTFT 也更慢；容量只有 370k–455k。因此不作为默认，保留为可选实验开关。
- **page-major KV 布局**：R047 在参数解析阶段被拒绝；`--enable-page-major-kv-layout` 需要 `--enable-unified-memory`，而 Qwen4-Exp 的模型级 override 明确抛出 “Qwen4-Exp does not support --enable-unified-memory yet”。不开发内核绕过。
- **Host 5 GiB / FlashInfer linear decode / lazy / INT8 / track512**：均已在 R035–R046 记录为无收益或不支持，R054 不重复。

## R057–R063：仍无综合超越

- **NGRAM 不支持**：默认 draft 12 超 QSA ratio；改 draft 4 后仍报 `Qwen4 PLE does not support NGRAM speculation`。
- **chunked prefill 8192**：R059 的 262k 冷 TTFT 降至 107.655 秒（-5.0%）、热双并发 33.24 tok/s（+2.8%），但最低 MemAvailable 只有 15.41 GiB，不能作为默认；R060/R061 的补救也未达标。
- **R062（chunk 8192 + MEMFRAC 0.77）**：最低 17.26 GiB、262k 冷 109.603 秒，是长上下文延迟优先的备选，但容量只有 722,880，比 R054 中位少 15.2%。
- **连续 decode 两步**：R063 冷 25.789、热 32.24，无收益。

因此 R054 保持综合最优。详见 [R057–R063 优化结果](OPTIMIZATION_R057_R063.md)。

## 专用 vLLM + FP4-Local 整方案对照

已实测 `eugr/spark-vllm-b12x` + NVIDIA FP4-Local（MIXED_PRECISION）。最佳 vLLM 变体为 V008（0.77、关闭 MTP、conv BF16）：设备 KV **1,120,703 tokens**，冷 8k/256 **31.114 tok/s**、热 2 并发 **41.88 tok/s**、262k 冷 **105.458 秒**，运行时最低 **16.86 GiB**，MMLU 53/57。它在新请求/近期热请求上超过 R054，但压力回放仍约 4.36 秒，远差于 R054 的 0.737 秒。offload 4 GiB 只把回放均值改善到 3.145 秒且内存不达标；SSM BF16 被后端拒绝。

**结论：新请求/近期热请求可选 V008；通用前缀复用和缓存压力下的生产默认仍是 R054。** 详见 [专用 vLLM + FP4-Local 整方案对照](VLLM_FP4_LOCAL_RESULTS.md)。

## 四台 Spark 建议

设备：`spark-33a3`（实测）、`spark-ef42`（体检）、`spark-ce63`、`spark-c3bc`（未接入）。

推荐四台各运行一个 R054 TP1 实例，每台 2 并发，总并发 8。按 R054 单机三轮中位值线性估算，合计约 **127.9 tok/s、8.53 req/s**；设备 KV 合计约 **3.41M tokens**，Host 合计约 **1.15M tokens**。这不是四机实测。若四台都按 16.2 GiB 的最低 MemAvailable 运行，Host 余量偏薄，建议逐台先跑同一 262k+压力组合再接入。

## 最终启动命令

已有正确 PLE 和 reuse view 时直接启动：

```bash
RUN=R064-fp8kv-mamba-bf16-direct-mf078-YYYYMMDD \
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
bash /workspace/qwen38-flash-next-tp1/deploy/serve.sh
```

停止：`docker stop --time 10 zj2-tp1-R064-fp8kv-mamba-bf16-direct-mf078-YYYYMMDD`

PLE 按 checkpoint 固定目录，RUN 串行；KV、运行缓存、日志和结果按 RUN 分开。PLE mtime 仍可能变化，不宣称零写入。

## 证据与复现

- [复现说明](REPRODUCE.md)
- [MTP 与 page-major 负结果](MTP_RESULTS.md)
- [GPU-only 冷请求与长题](GPU_ONLY_COLD_RESULTS.md)
- [缓存层级与逐目标覆盖率](CACHE_LEVEL_RESULTS.md)
- [长上下文结果](LONG_CONTEXT_RESULTS.md)
- [指标定义](METRICS.md)
- [需求逐项状态](REQUIREMENTS_STATUS.md)
- 原始数据：`../results/R001-*` 至 `../results/R056-*`；最终证据为 R054–R056 与 `../results/aggregate-r054-cold/`。