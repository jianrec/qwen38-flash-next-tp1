# 精度、内存、有效 token、速度与质量

本文所有数字都来自单机 TP1、最多 2 并发。未做四机实测。

## 1. 最终配置的实际精度

| 层 / 模块 | 实际精度或配置 | 说明 |
|---|---|---|
| 主权重 | NVFP4（ModelOpt） | MoE experts 为主；attention、shared expert 等保留 BF16 |
| 主 KV | FP8 E4M3 | scale 默认 1.0，未做校准 |
| QSA indexer | BF16 | compressed QSA；GB10 SM121 不支持 FP8 indexer |
| Mamba SSM | BF16 | FP32 参考约 1.16 GB，BF16 约 0.58 GB |
| PLE | FP8，scale 0.0002 | 47.68 GiB 文件后端，按 checkpoint 固定目录复用 |
| 计算 | `dtype=auto`，FlashInfer | TP1，单实例，最多 2 并发 |
| Host 缓存 | 4 GiB，约 288,576 tokens | HiCacheFile 64 GiB，write_back + wait_complete |
| I/O | direct + page_first_direct | chunked prefill 4096，Mamba track 256，extra_buffer |

软件版本：

- SGLang `0.0.0.dev1+gaa5551d9b`
- PyTorch `2.15.0.dev20260818+cu134`
- Transformers `5.19.0`
- FlashInfer `0.7.0.post1`
- ModelOpt `0.47.0`

## 2. 有效 token 与内存

### 2.1 参考配置

| 配置 | 设备 tokens | 日志 K+V (GiB) | Host tokens | 说明 |
|---|---:|---:|---:|---|
| R019 | 607,808 | 7.391 | 261,888 | FP8 KV，Mamba FP32，MEMFRAC 0.76 |
| R033 | 697,920 | 8.487 | 284,928 | Mamba BF16，MEMFRAC 0.76 |
| R034 | 698,560 | 8.495 | 284,928 | R033 + direct I/O |
| **R054** | **852,800** | **9.76** | **288,576** | **R034 + MEMFRAC 0.78** |

R054 是三次独立重启的中位数，单次范围为：

- 设备 tokens：843,328–882,368
- 日志 K+V：9.66–10.10 GiB
- Host tokens：288,384–289,152

### 2.2 增量与归因

| 步骤 | 变量 | 设备 tokens 变化 | 内存变化 | 速度或回放变化 |
|---|---|---|---:|---|
| R019→R033 | Mamba SSM FP32→BF16 | 607,808→697,920（**+90,112，+14.8%**） | SSM 1.16→0.58 GB；自动池把空间继续分给 KV，KV +约 1.1 GiB | 2 并发 31.113→32.006（+2.9%） |
| R033→R034 | HiCache I/O kernel→direct、page_first→page_first_direct | 697,920→698,560（+640，+0.09%） | KV 基本不变 | 回放 TTFT 0.804→0.617（-23.2%）；回放吞吐 15.723→18.256（+16.1%）；2 并发 +1.7% |
| R034→R054 | MEMFRAC 0.76→0.78 | 698,560→852,800（**+154,240，+22.1%**） | 日志 K+V 8.495→9.76 GiB（+1.265 GiB） | 2 并发 32.541→31.974（-1.7%）；262k 冷 116.318→113.318（-2.6%） |

**不能把 R054 的 +22.1% 归给 FP8 KV。** R054 相对 R034 只改了静态内存比例；Mamba BF16 和 direct I/O 是更早的独立步骤。

## 3. FP8 KV 的独立收益

### 3.1 固定池

| 指标 | BF16 KV | FP8 E4M3 KV | 变化 |
|---|---:|---:|---|
| 固定设备 tokens | 65,536 | 65,536 | 不变 |
| KV 记录 | 1.548 GiB | 0.798 GiB | **-0.750 GiB（-48.45%）** |
| 相同 K/V 空间可容纳 | 65,536 | 131,072 | **+100%** |

### 3.2 自动池

| 指标 | BF16 KV | FP8 KV | 变化 |
|---|---:|---:|---|
| 自动设备 tokens（16k context） | 436,544 | 835,520 | **+398,976（+91.39%）** |

### 3.3 速度

固定 131,072-token 池、GPU-only、8k/256 双并发，三次独立重启中位：

| 指标 | BF16 KV | FP8 KV | 变化 |
|---|---:|---:|---|
| TTFT | 5.645 s | 5.553 s | **-1.62%** |
| 单请求 decode | 17.430 tok/s | 17.514 tok/s | **+0.48%** |
| 实例输出吞吐 | 25.660 tok/s | 25.838 tok/s | **+0.69%** |

**结论：FP8 KV 的主要收益是容量，速度基本持平，不是大幅加速。**

### 3.4 质量

- 同一组冷请求：BF16/FP8 各 **60/60** 正确。
- 8k/32k 长题：**20/20**，输出逐字一致。
- 该结论只覆盖这些题和当前 unit scale，不外推到所有长上下文任务。

## 4. R054 长上下文与压力

| 场景 | 输入 / 压力 | 结果 | 正确性 |
|---|---|---|---|
| 原生 262k 冷请求 | 261,925 tokens | TTFT **113.318 s** | start/middle/end 全对 |
| 262k 热请求 | 261,888 cached | TTFT **0.647 s** | 与冷请求一致 |
| 262k 热回放 | 261,888 cached | TTFT **0.241 s** | 逐字一致 |
| 480,718-token 压力 | 80 个压力文档 + 4 个目标 | **91/91 正确，4/4 完整回放** | 逐字一致 |
| 压力回放 | 每目标 device 0 + host 4,096 + storage 1,600 | 吞吐 **17.251 tok/s**，平均 TTFT **0.737 s** | 5,696/5,696 tokens |

压力回放的完整覆盖来自 Host + HiCacheFile 文件层，不是设备池单独完成。storage-only 覆盖率约 28%，不能把“storage-only 未达 95%”误判为回放失败。

## 5. 质量与稳定性

| 项目 | 结果 | 说明 |
|---|---|---|
| MMLU | **52/57** | 57 题全部解析，无错误 |
| GSM8K | **176/200**（继承 R033） | R054 未重跑；同权重、同 KV、同计算路径 |
| 8k/32k 长题 | **20/20** | 与 R032 基线逐字一致 |
| 压力题 | **91/91** | 输出逐字一致 |
| 最低 MemAvailable | **16.19 GiB** | 高于 16 GiB 预留线，余量约 0.19 GiB |
| 最大 Swap 增长 | **10.44 MiB** | 无 OOM；未做 2 小时持续换页统计 |

稳定性结论只覆盖约 1 小时的 262k、压力、冷启动和质量组合负载。Swap 净增长很小不等于没有换页；生产环境应继续看 `/proc/pressure/memory` 和 swap in/out。

## 6. 可选 vLLM FP4-Local

V008 是另一套完整方案，不是同一个 checkpoint 的单变量对照。

| 指标 | R054（SGLang） | V008（vLLM） | 差异 |
|---|---:|---:|---|
| 设备 KV tokens | 852,800 | **1,120,703** | +31.4% |
| 冷 8k/256 双并发 | 25.735 tok/s | **31.114 tok/s** | +20.9% |
| 热 2 并发 | 31.974 tok/s | **41.88 tok/s** | +31.0% |
| 262k 冷 TTFT | 113.318 s | **105.458 s** | -6.9% |
| 压力回放 TTFT | **0.737 s** | 约 4.36 s | 约 5.9 倍慢 |
| MMLU | 52/57 | **53/57** | 单轮多 1 题，不能视为稳定增益 |
| 最低运行时内存 | 16.19 GiB | 16.86 GiB | 都高于 16 GiB |

V008 关闭 MTP，只把 Mamba conv state 降到 BF16，SSM 仍为 FP32；它适合新请求/近期热请求，不适合需要 Host + 文件深层回放的工作负载。

## 7. 指标定义

- `T_pool = server_info.max_total_num_tokens`
- `T_single = min(T_pool, context_length)`
- `T_avg@2 = min(context_length, floor(T_pool / 2))`
- `C_full = cached_tokens / prompt_tokens`
- `C_storage = storage_tokens / prompt_tokens`

整体覆盖率按目标 token 总和相除，不取各请求百分比的算术平均。

R054 的 `T_pool` 中位为 852,800，但 `context_length=262,144`，所以 `T_single=262,144`、`T_avg@2=262,144`；不能把 852,800 当作单请求上下文。

## 8. 未测与不支持

- FP8 权重：官方约 172.8 GiB，单台 GB10 无法在 KV 和运行时之外常驻；没有可给出的权重节省数值。
- FP4 KV：QSA warmup 报 `float4_e2m1fn_x2` 类型错误。
- QSA FP8 indexer：仅支持 SM90/SM100，GB10 SM121 不支持。
- page-major / unified memory：Qwen4-Exp 明确拒绝 unified memory。
- NGRAM 投机解码：Qwen4 PLE 不支持 NGRAM speculation。
- MTP/EAGLE 默认：热短输出快，但冷 256 降到 19.76 tok/s，容量 370k–455k，不作为默认。
- INT8 Mamba、lazy、track512：不兼容或无收益。
- 1M 单请求、四机实测、TP2/TP4 实测：未做。