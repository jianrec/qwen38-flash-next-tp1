# Qwen3.8 Flash Next · 单机 TP1 部署与优化

这是一个可复现的单机 TP1 配置仓库，目标是在 **1 × NVIDIA GB10（约 128 GiB UMA）** 上运行 `Qwen3.8 Flash Next`，并给出 FP8 KV、Mamba BF16、Host/文件缓存和静态内存比例的实测结果。

**当前综合最优：R054**

- NVFP4 权重，FP8 E4M3 主 KV
- Mamba SSM BF16
- `MEMFRAC=0.78`，`context_length=262144`
- Host 4 GiB + HiCacheFile 64 GiB
- direct I/O + `page_first_direct`
- TP1，单实例，最多 2 并发

## 关键结果

| 指标 | R054 |
|---|---:|
| 设备 KV 容量（三次重启中位） | **852,800 tokens**（843,328–882,368） |
| 比 R034（698,560） | **+154,240（+22.1%）** |
| 比 R019（607,808） | **+244,992（+40.3%）** |
| 日志 K+V 分配中位 | **9.76 GiB**（9.66–10.10） |
| Host tokens 中位 | **288,576** |
| 8k/256 冷双并发 | **25.735 tok/s**（25.657–25.802） |
| 热 2 并发（三轮中位） | **31.974 tok/s** |
| 262k 冷 TTFT | **113.318 s** |
| 262k 热回放 | **0.241 s** |
| 压力回放 | **91/91 正确，4/4 完整回放** |
| 最低 MemAvailable | **16.19 GiB**，无 OOM |
| 质量 | MMLU 52/57；长题 20/20 逐字一致；压力 91/91 |

完整表格、精度变化和归因见 [RESULTS.md](RESULTS.md)。

## 精度与内存变化

| 变化 | 实际结果 |
|---|---|
| FP8 KV（固定 65,536 tokens） | KV 1.548 → 0.798 GiB，少 **0.750 GiB（-48.45%）** |
| FP8 KV（自动池） | 436,544 → 835,520 tokens，**+91.39%** |
| FP8 KV 速度 | TTFT **-1.62%**，decode **+0.48%**，实例吞吐 **+0.69%** |
| Mamba SSM BF16 | 1.16 → 0.58 GB；R019→R033 容量 **+14.8%** |
| MEMFRAC 0.76→0.78 | R034→R054 容量 **+22.1%**；速度基本持平 |
| direct I/O | 回放 TTFT **-23.2%**，回放吞吐 **+16.1%** |

**FP8 权重未测。** 官方 FP8 权重约 172.8 GiB，单台 GB10 无法在 KV 和运行时之外常驻；本仓库不把 NVFP4 或 FP4-Local 的结果冒充 FP8 权重。

## 仓库结构

```text
deploy/                 SGLang 启动、PLE 复用、就绪检查、smoke
bench/                  冷请求、262k、压力、性能、质量脚本
vllm/                   可选 vLLM FP4-Local 启动脚本
results/                R054 / 基线的关键 JSON、CSV、日志
docs/                   交付报告、复现说明、负结果
README.md               本文件
RESULTS.md              精度、内存、token、速度、质量明细
REPRODUCE.md            从零复现步骤
PREREQUISITES.md        模型、镜像、硬件和磁盘要求
SECURITY.md             不含权重、凭证和内部地址
```

## 快速开始

```bash
git clone https://github.com/jianrec/qwen38-flash-next-tp1.git
cd qwen38-flash-next-tp1

export MODEL=/models/qwen38-flash-next-serving-bf16mtp-v1
export IMAGE=<your-sglang-image>
export ALLOW_HOST=$(hostname -s)

bash deploy/prepare_ple_reuse.sh
RUN=R064-fp8kv-mamba-bf16-direct-mf078-$(date +%Y%m%d) \
KV_DTYPE=fp8_e4m3 MAMBA_SSM_DTYPE=bfloat16 MAX_TOTAL_TOKENS=auto \
CONTEXT_LENGTH=262144 MEMFRAC=0.78 PLE_REUSE_ONLY=1 HICACHE=1 \
HICACHE_STORAGE=file HICACHE_SIZE_GIB=4 HICACHE_IO_BACKEND=direct \
HICACHE_MEM_LAYOUT=page_first_direct PREFILL_GRAPH=0 PLE_RSS_BUDGET_GIB=8 \
bash deploy/serve.sh
```

完整步骤和测试命令见 [REPRODUCE.md](REPRODUCE.md)。

## 适用范围

- 本仓库只覆盖 **单机 TP1**；四台 Spark 的组合建议是设计估算，不是实测。
- 模型权重、私有 SGLang 镜像、PLE 大表和运行缓存 **不包含** 在本仓库中。
- 结果来自单台 `spark-33a3`；不同机器的 UMA 余量、PLE 页缓存和磁盘状态会让容量与 TTFT 有小幅波动。
- 不声称支持 FP4 KV、QSA FP8 indexer、unified memory、NGRAM 或 1M 上下文。

## 引用

如果这个配置对你有帮助，可以引用本仓库：

```bibtex
@misc{jianrec2026qwen38tp1,
  title  = {Qwen3.8 Flash Next single-node TP1 deployment and optimization},
  author = {jianrec},
  year   = {2026},
  url    = {https://github.com/jianrec/qwen38-flash-next-tp1}
}
```