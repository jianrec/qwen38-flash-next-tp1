# MTP 投机解码与 page-major 布局负结果

更新：2026-11-13 03:00（北京时间）

## 结论

**MTP/EAGLE 不作为默认配置。** checkpoint 自带 NEXTN draft，SGLang 也有 `Qwen4ExpForCausalLMMTP` 专门实现；投机解码输出精确，热短输出 2 并发可到约 49 tok/s（相对 R034 +49%），但 256-token 冷输出的接受长度只有 1.16（接受率 5.4%），冷 8k/256 双并发降到 19.76 tok/s（相对 R054 的 25.74 低 23.3%），262k 冷 TTFT 也更慢；容量只有 370k–455k。它适合“短输出、热前缀、容量不敏感”的场景，不适合本项目的默认长上下文与冷请求目标。

**page-major KV 布局在当前模型上不可用。** `--enable-page-major-kv-layout` 要求 `--enable-unified-memory`；Qwen4-Exp 的模型级 override 明确拒绝 unified memory。没有绕过模型门。

## MTP 配置与容量

统一参数：`SPECULATIVE_ALGORITHM=EAGLE`、`SPECULATIVE_NUM_STEPS=3`、`SPECULATIVE_EAGLE_TOPK=1`、`SPECULATIVE_NUM_DRAFT_TOKENS=4`。draft 使用 checkpoint 内 `mtp.*` 权重；SGLang 日志显示 draft 以 NVFP4 路径加载，实际占用约 0.47–0.71 GB。

| RUN | MEMFRAC | Host | 设备 tokens | 说明 |
|---|---:|---:|---:|---|
| R050 | 0.77 | 4 GiB | 65,152 | 容量不足以放 262k 单请求 |
| R051 | 0.80 | 4 GiB | 370,304 | 可跑 262k；完整功能/质量验证 |
| R052 | 0.81 | 3 GiB | 423,232 | 262k 与压力通过 |
| R053 | 0.81 | 3 GiB | 455,616 | 三次重启间有波动；冷启动主证据 |

容量随 memfrac 的边际约 10 万 tokens / 0.01，但 draft 和 spec 的运行时预留会随配置变化，不能按单一线性公式外推。R052/R053 容量不同但配置相同，说明启动时可用内存本身有波动。

## 速度

### 热短输出（8k 前缀，2 并发，64 max，实际约 16 tokens）

| RUN | 吞吐 | TTFT | TPOT |
|---|---:|---:|---:|
| R034（无 MTP） | 32.541 tok/s | 0.223 s | 0.0498 s |
| R050 | 48.96 / 48.23 tok/s | 0.252 / 0.260 s | 0.0256 s |
| R051 | 48.62 / 48.20 tok/s | 0.256 / 0.261 s | 0.0257 s |
| R053 | 49.20 / 49.49 tok/s | 0.251 / 0.247 s | 0.0255 s |

热短输出相对 R034 约 **+49%**。此时 `spec_accept_length` 约 2.30，`spec_accept_rate` 约 43.5%。

### 冷 8k/256 双并发（独立重启、唯一前缀）

| RUN | 实例吞吐 | 单请求 decode | TPOT 中位 | 接受长度 |
|---|---:|---:|---:|---:|
| R054（无 MTP） | 25.735 tok/s | 17.716 tok/s | 0.05648 s | — |
| R053（MTP） | **19.76 tok/s** | 13.36 tok/s | 0.07485 s | 1.16（5.4%） |

冷 256-token 输出大部分是空白续写，MTP 接受率骤降，verify 开销超过收益，实例吞吐 **-23.3%**。这是 MTP 不作为默认的主要原因。

### 长上下文与回放

| RUN | 262k 冷 TTFT | 262k 热 TTFT | 262k 回放 TTFT | 压力回放吞吐 |
|---|---:|---:|---:|---:|
| R054（无 MTP） | 113.318 s | 0.647 s | 0.241 s | 17.251 tok/s |
| R051（MTP） | 121.439 s | 0.597 s | 0.260 s | 16.774 tok/s（并发 1） |
| R052（MTP） | 120.382 s | 0.562 s | 0.252 s | 20.821 tok/s（并发 2） |

MTP 的 262k 冷 TTFT 慢约 6–7%；热回放略慢或持平；压力回放吞吐在并发 2 时可到 20.82 tok/s（+20.7%），但容量只有 R054 的一半左右。

## 质量

- R051：20/20 长题正确，且 20/20 输出与 R032（Mamba BF16、无 MTP）逐字一致。
- R051：多轮测试所有回答 exact，`reference_trace_exact=true`；follow-up 仍是 device 4096 + storage 1600 = 5696 tokens，仅“storage-only≥95%”门不适用（与 R034 相同）。
- R051：压力 91/91 正确、4/4 完整回放、91/91 逐字一致。
- 投机解码是精确验证，不改变贪心输出；这与上述逐字一致结果相符。

## 内存与稳定性

- R052 262k 期间最低 MemAvailable **16.62 GiB**，全程最低 16.58 GiB，Swap 0。
- R053 冷启动全程最低 **16.45 GiB**，Swap 增长 <11 MiB。
- R051 压力阶段全程最低 15.82 GiB（连续 5 秒采样），说明 0.80/Host4 的内存余量已偏薄。
- 无 OOM；但 MTP 的运行时预留使容量对 memfrac 非常敏感，0.77 时只有 65k tokens，不能用于 262k。

## page-major 布局

R047 使用 `PAGE_MAJOR_KV_LAYOUT=1` 启动，参数解析阶段报：

```text
AssertionError: --enable-page-major-kv-layout without --enable-unified-memory is temporarily unsupported ...
```

源码中 `handle_page_major_kv_layout` 要求 unified memory；而 `arg_groups/model_overrides/qwen4_exp.py` 对 `Qwen4ExpForConditionalGeneration` 直接抛出：

```text
ValueError: Qwen4-Exp does not support --enable-unified-memory yet
```

因此当前模型无法使用 page-major 或 unified memory。原始失败日志在 `results/R047-pagemajor-unsupported-20261113/`。

## 复现

MTP 启动示例：

```bash
RUN=R058-mtp-eagle-YYYYMMDD \
KV_DTYPE=fp8_e4m3 \
MAMBA_SSM_DTYPE=bfloat16 \
MAX_TOTAL_TOKENS=auto \
CONTEXT_LENGTH=262144 \
MEMFRAC=0.81 \
PLE_REUSE_ONLY=1 \
HICACHE=1 \
HICACHE_STORAGE=file \
HICACHE_SIZE_GIB=3 \
HICACHE_IO_BACKEND=direct \
HICACHE_MEM_LAYOUT=page_first_direct \
PREFILL_GRAPH=0 \
PLE_RSS_BUDGET_GIB=8 \
SPECULATIVE_ALGORITHM=EAGLE \
SPECULATIVE_NUM_STEPS=3 \
SPECULATIVE_EAGLE_TOPK=1 \
SPECULATIVE_NUM_DRAFT_TOKENS=4 \
bash /workspace/qwen38-flash-next-tp1/deploy/serve.sh
```

测量使用 `bench/cold_concurrent.py`、`bench/phase1_benchmark.py`、`bench/concurrent_hot_benchmark.py`、`bench/cache_acceptance.py`、`bench/long_quality.py` 和 `bench/multiturn_cache_acceptance.py`；原始数据在 R050–R053。