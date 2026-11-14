# R057–R063 后续优化结果

更新：2026-11-13 12:40（北京时间）

## 结论

**R054 仍是综合最优。** 本轮没有找到在容量、速度、内存安全上同时超过它的配置。NGRAM 被模型明确拒绝；chunked prefill 8192 能加快长上下文，但会把最低内存压到 16 GiB 以下；连续 decode 两步没有可测收益。

## 结果表

| RUN | 相对 R054 的变化 | 设备 tokens | 262k 冷 TTFT | 冷 8k/256 双并发 | 热 2 并发 | 最低 MemAvailable | 结论 |
|---|---|---:|---:|---:|---:|---:|---|
| R057 | NGRAM 默认 draft 12 | 520,896 | 未形成服务 | — | — | — | QSA 要求 draft≤4 |
| R058 | NGRAM draft 4 | 616,320 | 未形成服务 | — | — | — | Qwen4 PLE 不支持 NGRAM |
| R059 | chunk 8192 | 885,824 | **107.655 s（-5.0%）** | 26.312 tok/s（+2.2%） | 33.24 tok/s（+2.8%） | **15.41 GiB** | 速度最快，但内存不达标 |
| R060 | chunk 8192 + PLE RSS 6 | 846,912 | 113.305 s | — | — | **15.54 GiB** | 冷启动收益消失，拒绝 |
| R061 | chunk 8192 + Host 3 | 884,800 | 112.693 s | — | — | **15.37 GiB** | Host 降低不能解决峰值内存，拒绝 |
| R062 | chunk 8192 + MEMFRAC 0.77 | 722,880 | 109.603 s（-3.3%） | — | 32.23 tok/s | 17.26 GiB | 可作长上下文速度备选，容量 -15.2% |
| R063 | continuous decode steps 2 | 845,760 | — | 25.789 tok/s | 32.24 tok/s | 17.21 GiB | 无收益，不采用 |
| **R054（基线）** | — | **852,800 中位** | 113.318 s | 25.735 tok/s | 32.34 tok/s | 16.19 GiB | **综合最优** |

冷/热列含义与主报告相同：冷列是约 8,160 input / 256 output 的独立冷请求双并发；热列是 8k 热前缀、64 max 的双并发 benchmark。

## NGRAM

- 默认参数启动时被 QSA 拒绝：`Qwen QSA requires speculative_num_draft_tokens <= the QSA compress ratio (4)`。
- 改成 draft 4 后仍失败：`NotImplementedError: Qwen4 PLE does not support NGRAM speculation`。
- 因此 NGRAM 与 MTP 不同，不是可切换的无权重加速开关；当前模型/后端不支持。

## chunked prefill 8192

- R059 证明它确实有用：262k 冷 TTFT 从 113.318 降到 **107.655 秒（-5.0%）**，热双并发从 32.34 提到 **33.24 tok/s（+2.8%）**，冷双并发从 25.735 提到 **26.312 tok/s（+2.2%）**。
- 但 262k 期间最低 MemAvailable 只有 **15.41 GiB**，低于项目 16 GiB 预留线。
- 降低 PLE RSS（R060）或 Host（R061）不能恢复内存，且 R060 把冷 TTFT 又拉回 113.305 秒。
- R062 用 MEMFRAC 0.77 换安全余量：最低 **17.26 GiB**，262k 冷 **109.603 秒**，但容量只有 722,880，比 R054 中位少 15.2%。如果业务更看重 262k 冷启动延迟，可以用 R062；如果更看重 KV 容量和综合收益，仍用 R054。

## 连续 decode 两步

R063 容量 845,760，冷 25.789 tok/s、热 32.24 tok/s，与 R054 的 25.735/32.34 基本相同；最低内存 17.21 GiB。没有可测收益，不加入最终配置。

## HiCacheFile 64→128 GiB

R054 的文件缓存实测约 **43 GiB**，尚未达到 64 GiB 上限。当前负载下扩大上限不会改变命中率或速度，所以本轮不把它写成收益。只有写入量接近 64 GiB 后，才需要把上限调到 128 GiB 并重测回放；磁盘目前仍有约 1.6 TiB 可用。

## 复现

R059/R062 的 chunk 参数已加入 `deploy/serve.sh`：

```bash
CHUNKED_PREFILL_SIZE=8192
```

R063 的开关为：

```bash
NUM_CONTINUOUS_DECODE_STEPS=2
```

NGRAM 的失败日志在 `results/R057-*`、`results/R058-*`；其余原始配置、日志和结果在 `results/R059-*` 至 `results/R063-*`。