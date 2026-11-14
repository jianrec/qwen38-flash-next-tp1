# 专用 vLLM + FP4-Local 整方案对照

更新：2026-11-14 12:25（北京时间）

## 结论

**专用 vLLM 确实有效，而且是“按模块混合精度”的现成实现：NVFP4 routed experts + MXFP8 attention + BF16 其他模块。它能明显提速，并给出更大的设备 KV 池；但它没有 R054 的 Host+文件深层回放能力，所以不能只凭热请求速度就替换 R054。**

- **新请求、唯一前缀、近期完全相同提示词的热请求**：V008 明显优于 R054。
- **同前缀不同后缀、长时间压力后的前缀回放**：R054 明显更好。
- 因此：**高吞吐新请求/近期热请求可选 V008；通用前缀复用和缓存压力下的生产默认仍是 R054。**

## 方案与下载

- 模型：`nvidia/Qwen3.8-Flash-Next-FP4-Local`，`Qwen4ExpForConditionalGeneration`，MIXED_PRECISION。
- 量化映射：438 个 MXFP8 模块、58 个 W4A16_NVFP4 模块、49 个 NVFP4 模块；其余模块按 ignore 保留。
- 引擎：`eugr/spark-vllm-b12x:latest`，B12x 专用内核，V2 Model Runner。
- 镜像通过 `docker.m.daocloud.io` 拉取，模型通过 `hf-mirror.com` 下载；镜像 config digest 和全部 26 层 digest 与官方一致。
- 本地模型：`/models/Qwen3.8-Flash-Next-FP4-Local`。
- 启动脚本：`vllm/serve_fp4_local.sh`，使用同一 `serial.lock` 和 98 GiB 容器限制。

## 四轮配置

| RUN | 引擎配置 | 设备 KV tokens | 主要结论 |
|---|---|---:|---|
| V001 | 官方 0.8 + MTP 4 | 1,018,579 | 热请求 51.78 tok/s，262k 运行时最低约 13 GiB |
| V002 | 0.78 + MTP 4 | 858,325 | 运行时约 16.4 GiB，但容量下降且冷 256 偏慢 |
| V003 | 0.78 + 关 MTP | 1,192,830 | 容量更大、冷/热更快，262k 运行时最低 15.41 GiB |
| V004 | 0.77 + 关 MTP | 1,112,040 | 速度、容量和运行时内存的折中 |
| **V008** | **0.77 + 关 MTP + conv BF16** | **1,120,703** | **当前最佳 vLLM 变体：容量、速度、质量与内存折中最好** |

MTP 在当前 vLLM 上也呈现同样规律：热短输出更快，但占用 draft/KV 预留，冷长输出和容量变差。V008 关闭 MTP、只把 conv state 降到 BF16 后，在容量、冷请求、热请求和质量之间最平衡。

## V008 对 R054 的核心结果

| 指标 | R054（SGLang） | V008（vLLM） | 差异 |
|---|---:|---:|---|
| 设备 KV tokens | 852,800（中位） | **1,120,703** | **+31.4%** |
| 冷 8k/256 双并发 | 25.735 tok/s | **31.114 tok/s** | **+20.9%** |
| 热 2 并发（8k 热前缀） | 31.974 tok/s | **41.88 tok/s** | **+31.0%** |
| 单请求 shared-prefix | 17.059 tok/s | 18.432 tok/s（V004 同路径） | +8.0% |
| 单请求 unique-prefix | 13.484 tok/s | 18.414 tok/s（V004 同路径） | +36.6% |
| 262k 冷 TTFT | 113.318 s | **105.458 s** | **-6.9%** |
| 262k 热回放 TTFT | 0.241 s | 0.492 s | 慢 104% |
| 压力回放 TTFT（4 目标均值） | 0.737 s | 约 4.36 s | 约 5.9 倍慢 |
| 压力回放吞吐 | 17.251 tok/s | 6.468 tok/s | -62.5% |
| MMLU | 52/57 | **53/57** | 多 1 题（单轮，不能视为稳定增益） |
| 8k/32k 长题 | 20/20 逐字一致 | 20/20 逐字一致 | 相同 |
| 压力答案 | 91/91 正确 | 91/91 正确 | 相同 |
| 最低运行时 MemAvailable | 16.19 GiB | 16.86 GiB | 都高于 16 GiB |

## 为什么不直接替换 R054

1. **通用前缀复用更弱**：V008 对完全相同提示词的热请求很快，但对同前缀不同后缀的复用明显弱于 SGLang；`phase1_benchmark` 的 shared-prefix TTFT 约 2.18 秒，而 R054 约 0.265 秒。
2. **压力后回放重算**：480,718-token 压力后，V008 的四个回放都约 4.33–4.39 秒，R054 平均 0.737 秒。
3. **没有 Host+文件层**：当前 vLLM 配置只有设备 prefix cache，不能提供 R054 的 Host HiCache + HiCacheFile 深层回放；CPU offload 4 GiB 也只改善到 3.145 秒。
4. **启动瞬时内存风险**：vLLM 模型加载期间出现瞬时低 MemAvailable；运行时恢复并稳定，但启动阶段仍应单独监控。
5. **MMLU 波动**：V008 为 53/57，R054 为 52/57，但不同 vLLM 变体出现 51–53，单轮不能证明质量提升。

## 推荐用法

- 如果业务以**新请求、唯一前缀、近期完全相同提示词的热请求**为主，V004 是更快且容量更大的整方案：冷 8k +20.9%、热 2 并发 +28.6%、262k 冷启动 -6.3%。
- 如果业务需要**多轮对话、不同后缀的长前缀复用、压力后的稳定回放**，继续用 R054。
- 若要把 V004 变成真正的通用替代，需要再补 Host/外部 KV 缓存连接器和与 Mamba 状态匹配的回放验证；当前配置没有这项能力。

## 启动 V008

```bash
cd /workspace/qwen38-flash-next-tp1
RUN=V009-fp4local-vllm-nomtp-mem077-conv-bf16-YYYYMMDD \
GPU_MEM_UTIL=0.77 \
MTP=0 \
MAMBA_CACHE_DTYPE=bfloat16 \
MAMBA_SSM_CACHE_DTYPE=float32 \
bash vllm/serve_fp4_local.sh
```

停止：`docker stop --time 10 zj2-vllm-V009-fp4local-vllm-nomtp-mem077-conv-bf16-YYYYMMDD`

原始配置、日志和结果在 `results/V001-*` 至 `results/V008-*`。

## 追加：vLLM 的 CPU offload 能不能替代 Host+文件层

**不能完全替代，只能部分改善。** 源码里有 Qwen QSA hybrid-state 转移支持，但 native CPU offload 与 R054 的 Host+HiCacheFile 不是同一层级。

- V005（0.76 + 4 GiB offload、默认 checkpoint policy）启动被拒绝：Qwen QSA 要求 `recurrent_checkpoint_policy=aligned`。
- V006（0.76 + 4 GiB offload + aligned）启动成功，报告设备 KV **1,260,908 tokens**。
- V006 压力回放 TTFT 为 2.993、4.530、2.341、2.716 秒，均值 **3.145 秒**，比 V004 的约 4.39 秒好约 28%，但仍远差于 R054 的 **0.737 秒**；回放吞吐 **6.271 tok/s**，没有实质追上。
- V006 压力期间最低 MemAvailable **14.67 GiB**，低于 16 GiB 安全线。
- 镜像中没有 `lmcache` 模块，当前不能直接复现 LMCache SSD 层；即便再装 LMCache，也必须重新验证 Mamba/GDN 与 QSA side-state 的恢复，不能只搬 attention KV。

因此，**vLLM 可以加 CPU offload，但当前不能靠它获得 R054 的深层回放能力。** 新请求/近期热请求仍可用 V004；通用前缀复用和压力回放仍选 R054。

## 追加：Mamba 状态精度实验

- V007（SSM BF16）启动失败：`ValueError: state_dtype must be torch.float32`。当前 vLLM 的 GDN/B12x 后端要求 SSM state FP32，不能像 SGLang 那样把 SSM 降到 BF16。
- V008（conv BF16、SSM FP32）启动成功，是当前 vLLM 的最佳折中：
  - 设备 KV **1,120,703 tokens**，比 V004 多 0.8%。
  - 冷 8k/256 **31.114 tok/s**，热 2 并发 **41.88 tok/s**，与 V004 基本持平。
  - 262k 冷 **105.458 秒**，略快于 V004 的 106.138 秒；回放 0.492 秒，与 V004 相近。
  - MMLU **53/57**（R054 为 52/57，但单轮波动可能贡献一部分），长题 20/20 逐字一致。
  - 最低运行时 MemAvailable **16.86 GiB**，压力回放仍约 4.36 秒，未改善。

**最终精度结论：vLLM 只能把 conv state 降到 BF16，收益很小；SSM BF16 不支持。V008 是新请求/近期热请求的首选 vLLM 配置，但通用前缀复用仍选 R054。**

## 追加：Mamba checkpoint retention 256 不可直接迁移

V009 尝试把 SGLang 的 `mamba_track_interval=256` 映射到 vLLM 的 `prefix_cache_retention_interval=256`，启动被拒绝：

```text
ValueError: prefix_cache_retention_interval (256) must be non-negative and a multiple of scheduler_block_size (2960).
```

vLLM 当前混合页的 scheduler block size 为 **2960**，因此 256 不能直接使用；下一个可试值是 2960，但它比 SGLang 的 256 粗 11.6 倍，预计不能解决细粒度后缀复用。该方向未继续。