# 需求逐项回答与证据状态

更新：2026-11-13 03:00（北京时间）

## 最终状态

**最佳配置更新为 R054：R034 的全部配置不变，只把 `mem_fraction_static` 从 0.76 提到 0.78。** 三次独立重启的设备 KV 中位 **852,800 tokens**（843,328–882,368），262,144 context，Host 4 GiB + HiCacheFile，direct I/O，关闭 prefill graph。速度与 R034 持平，最低 MemAvailable 16.19 GiB，Swap 增长 10.44 MiB，无 OOM。

FP8 权重未测；FP4 KV、QSA FP8 indexer、page-major/unified memory 均因不支持而保留失败记录。MTP/EAGLE 已实测：热短输出 +49%，但冷 8k/256 -23%，容量 370k–455k，因此不作为默认。

## 逐项回答

| 需求问题 | 最终回答与证据 |
|---|---|
| FP8 权重节省多少，多少转给 KV？ | 未测。官方 FP8 权重约 172.8 GiB，单台 GB10 无法正常常驻；当前 NVFP4 权重不能冒充 BF16 或 FP8 权重。 |
| FP8 KV 少占多少内存？ | 固定 65,536：少 0.750 GB（48.45%）。固定 131k + Host 3：少 1.500 GB（48.47%）。 |
| Mamba BF16 节省多少？ | SSM 从 FP32 的 1.16 降至 0.58 GB，直接少 0.58 GB。自动池会继续分配 KV，因此 R033/R034 的 KV 记录比 R019 高约 1.1 GB，不是总分配下降。 |
| 有效 token 增加多少？ | 近似同 K/V 空间：65,536→131,072（+100%）。Mamba BF16 将 R019 的 607,808 提至 R033 的 697,920（+14.8%）。R054 的 memfrac 0.78 再提到中位 **852,800**，比 R034 的 698,560 多 **22.1%**，比 R019 多 **40.3%**。单次范围 843,328–882,368，不能把上限当固定值。 |
| 单请求和双并发上限？ | R054 的 $T_{pool}$ 中位 852,800，但 $T_{single}=262,144$；$T_{avg@2}=262,144$，不表示框架严格均分。 |
| 冷请求加快多少？ | 8k/256 双并发三次独立重启：中位 **25.735 tok/s**（25.657–25.802），与 R032 的 25.995 持平（-1.0%）；单请求 decode 17.716 tok/s，TTFT 中位 5.695 秒。 |
| FP8 KV 对 TTFT/decode/吞吐和质量？ | 固定 131,072-token 池、GPU-only、8k/256 双并发三轮：TTFT **-1.62%**，单请求 decode **+0.48%**，实例吞吐 **+0.69%**；自动池 436,544→835,520 tokens（+91.39%）。同组质量 BF16/FP8 各 60/60，8k/32k 长题 20/20 逐字一致。 |
| Mamba BF16 加快多少？ | R019→R033 的 2 并发吞吐从 31.113 提至 32.006（+2.9%），单请求从 13.522 提至 13.601。 |
| direct I/O 加快多少？ | R033→R034 回放 TTFT 从 0.804 降至 0.617（-23.2%），回放吞吐从 15.723 提至 18.256（+16.1%），常规 2 并发从 32.006 提至 32.541（+1.7%）。 |
| MEMFRAC 0.78 加快多少？ | 不加快，主要是容量：R034→R054 单请求 17.244→17.059（-1.1%），唯一前缀 13.535→13.484（-0.4%），2 并发 32.541→32.34（-0.6%），均在波动内；262k 冷 TTFT 116.318→113.318（-2.6%）。 |
| 原生 262k 是否可用？ | R054 输入 261,925 tokens，start/middle/end 全对；冷 TTFT 113.318 秒，热 0.647 秒，热回放 0.241 秒。未验证 1M。 |
| FP8 是否提高重复前缀复用？ | GPU-only 大压力下 BF16/FP8 都全部重算，不能靠自动大池 alone 提高命中。 |
| Host 的贡献？ | 小范围可命中 1,600 Host tokens；R054 压力回放的每目标层级为 device 0 + host 4,096 + storage 1,600，合计 5,696；Host 不能单独替代文件层。 |
| 文件缓存的贡献？ | 压力 4/4 完整回放、91/91 输出逐字一致；设备池扩大后目标更多落在 Host/文件层，回放吞吐 17.251 tok/s（R034 18.256，-5.5%），但仍远快于重算。 |
| 质量是否可接受？ | MMLU **52/57**；8k/32k 长题 **20/20 逐字一致**；R054 压力 91/91 逐字一致。GSM8K 未在 R054 重跑，R033 的 **176/200** 使用同一权重、KV 精度和计算路径。 |
| OOM、Swap 与稳定性？ | R054 最低 MemAvailable **16.19 GiB**，最大 Swap 增长 **10.44 MiB**，无 OOM；三次冷启动吞吐稳定在 25.657–25.802 tok/s。余量比 R034 薄 2.54 GiB，运行时应继续监控。 |
| FP4 KV 是否有效？ | R030 在 QSA warmup 抛出 `KeyError: float4_e2m1fn_x2`，当前 QSA 不支持，不开发新内核复现。 |
| QSA FP8 是否有效？ | R031 在启动前被拒绝，只支持 SM90/SM100；GB10 SM121 不支持。 |
| MTP/EAGLE 是否有效？ | 输出精确、热短输出 2 并发约 49 tok/s（+49%），但冷 8k/256 仅 19.76 tok/s（-23%），262k 冷 TTFT 慢 6–7%，容量 370k–455k；不作为默认。详见 `MTP_RESULTS.md`。 |
| NGRAM 是否有效？ | 不支持。默认 draft 12 超 QSA ratio；改 draft 4 后仍报 `Qwen4 PLE does not support NGRAM speculation`。 |
| chunked prefill 8192 是否有效？ | R059 的 262k 冷 TTFT 降至 107.655 秒（-5.0%）、热双并发 33.24 tok/s（+2.8%），但最低内存 15.41 GiB，不达标；R062 用 MEMFRAC 0.77 换 17.26 GiB，冷 109.603 秒、容量 722,880，可作长上下文速度备选，R054 仍综合最优。 |
| 连续 decode 两步是否有效？ | R063 冷 25.789、热 32.24，与 R054 基本相同，无收益。 |
| 按模块混合精度是否可行？ | 当前 SGLang checkpoint 已是 NVFP4+BF16 混合；若要自定义映射需离线重量化，`quantize_and_serve` 已禁用。FP4-Local 是现成的 MIXED_PRECISION（438 MXFP8、58 W4A16_NVFP4、49 NVFP4），已在 vLLM 实测。 |
| 专用 vLLM/FP4-Local 是否更优？ | 最佳变体 V008（0.77、关 MTP、conv BF16）设备 KV 1,120,703、冷 8k 31.114、热 41.88、262k 冷 105.458 秒，均超过 R054；但压力回放约 4.36 秒，MMLU 53/57。新请求/近期热请求可选 V008，通用前缀复用和缓存压力下仍选 R054。详见 `VLLM_FP4_LOCAL_RESULTS.md`。 |
| vLLM 能否加 Host+文件缓存层？ | native CPU offload 只能部分改善：V006（4 GiB + aligned）回放均值从约 4.39 降至 3.145 秒，仍远差于 R054 的 0.737 秒，且最低内存 14.67 GiB。镜像无 `lmcache`，当前不能复现 LMCache SSD 层；Mamba/GDN 与 QSA side-state 恢复必须单独验证。 |
| vLLM 的 Mamba 精度能否再降？ | SSM BF16 不支持：V007 报 `state_dtype must be torch.float32`。V008 仅 conv BF16 可用，容量 1,120,703、冷 31.114、热 41.88、262k 冷 105.458 秒、MMLU 53/57、最低 16.86 GiB；压力回放仍约 4.36 秒。 |
| vLLM 能否复现 SGLang track 256？ | 不能直接映射：V009 的 `prefix_cache_retention_interval=256` 被拒绝，vLLM 要求它是 scheduler_block_size **2960** 的倍数；下一个可用值 2960 比 256 粗 11.6 倍，未继续。 |
| page-major / unified memory 是否有效？ | 不支持。`--enable-page-major-kv-layout` 要求 `--enable-unified-memory`；Qwen4-Exp 模型 override 明确拒绝 unified memory。R047 保留失败日志。 |
| SEED/lazy 状态优化是否有效？ | 完整 SEED 需要训练 emitters 和低秩实现，官方尚未发布，当前镜像不支持。R035 的 `extra_buffer_lazy` 容量少 64 tokens，回放 TTFT 从 0.617 增至 0.645 秒，故不采用。 |
| 四台如何组合？ | 推荐 `spark-33a3`、`spark-ef42`、`spark-ce63`、`spark-c3bc` 各运行一个 R054 TP1，每台 2 并发，总并发 8。按单机三轮中位线性估算约 **127.9 tok/s、8.53 req/s**，设备 KV 合计约 3.41M tokens；**不是四机实测**。 |

## 配置与条件

- 设备：zj2 `spark-33a3` 实测；zj3 `spark-ef42` 仅体检；`spark-ce63`、`spark-c3bc` 未接入。
- 模型：`qwen38-flash-next-serving-bf16mtp-v1`，`Qwen4ExpForConditionalGeneration`。
- SGLang `0.0.0.dev1+gaa5551d9b`；PyTorch `2.15.0.dev20260818+cu134`；Transformers `5.19.0`；FlashInfer `0.7.0.post1`；ModelOpt `0.47.0`。
- 权重 NVFP4；主 KV FP8 E4M3；QSA BF16；Mamba BF16；PLE FP8，scale 0.0002；计算 `dtype=auto`、FlashInfer。
- TP1、单实例、最多 2 并发，容器 memory/memory-swap 98 GiB；`mem_fraction_static=0.78`。
- 请求包括约 8,160/256 冷请求三次独立重启、480,718-token 压力、15,528/32,325/261,925-token 长上下文、MMLU 57 题、20 条 8k/32k 长题和 91 条压力请求。

## 未测边界

- 四机实际聚合与新增两台接入。
- 1M 单请求；262k 已实测，不外推 1M。
- FP8 权重、校准 FP8 KV scale、FP4 KV 和 QSA FP8 indexer。
- R054 的独立 200 题 GSM8K；R033 的结果来自相同计算路径。
- 最终 R054 的 2 小时以上持续混合负载；已完成约 1 小时的 262k、压力、冷启动和质量组合，无 OOM。
- PLE mtime 仍变化，只保证固定单文件、串行访问和跳过源分片。
- MTP 的更长输出、不同 topk/steps 组合未穷举；当前 3/1/4 已足以判定不作为默认。

## 证据入口

- `FINAL_REPORT.md`：最终配置、结果表、四机建议和启动命令。
- `MTP_RESULTS.md`：MTP/EAGLE 与 page-major 负结果。
- `OPTIMIZATION_R057_R063.md`：NGRAM、chunked prefill、连续 decode 的最新结果。
- `VLLM_FP4_LOCAL_RESULTS.md`：专用 vLLM + FP4-Local 整方案对照。
- `PAPER_REVIEW.md`：论文适用性、FP4/QSA 不支持项和 Mamba BF16 结果。
- `SEED_REVIEW.md`：SEED 启发、官方未发布边界和 R035 lazy 负结果。
- `MAMBA_STATE_RESULTS.md`：INT8 不兼容、interval 对照与 ReplaySSM 不适用结论。
- `KV_LITERATURE.md`：单机 KV/状态管理文献地图。
- `CACHE_LEVEL_RESULTS.md`：Host/文件归因与逐目标覆盖率。
- `LONG_CONTEXT_RESULTS.md`：32k/262k 结果。
- `METRICS.md`：token 与覆盖率定义。
- `REPRODUCE.md`：最终启动、三轮冷启动、压力、性能和停止命令。
- `../results/`：R001–R056 原始配置、日志、请求和指标；三轮冷启动聚合在 `../results/aggregate-r054-cold/`。