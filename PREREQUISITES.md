# 前置条件与可复现边界

## 1. 硬件

| 项目 | 要求 |
|---|---|
| 设备 | 1 × NVIDIA GB10，约 128 GiB 统一内存（UMA） |
| 架构 | Blackwell，SM121 可运行当前自定义 SGLang 内核 |
| 磁盘 | 至少 80 GiB 空闲；建议 150 GiB 以上（含 PLE、Host/文件缓存） |
| 网络 | 本仓库的单机 TP1 不需要跨机通信 |
| 容器 | Docker + NVIDIA Container Toolkit |

本仓库的实测设备为 `spark-33a3`。`nvidia-smi` 在 GB10 上的内存读数不可靠，验收使用 `/proc/meminfo`、cgroup 和 SGLang 日志。

## 2. 模型 checkpoint

需要与实验相同的 checkpoint：

```text
qwen38-flash-next-serving-bf16mtp-v1
architecture: Qwen4ExpForConditionalGeneration
```

**模型权重不包含在本仓库中。** 你需要从模型所有者处取得完整 checkpoint，并保证：

- 主路径分片完整，`model.safetensors.index.json` 能解析；
- 有 128 个 `ple-fp8-*.safetensors`（PLE 源分片）；
- 有 245 个主加载路径的 shard（`layer-*`、`preserved-*`、scale）；
- `config.json` 中 `quantization_config.quant_algo=NVFP4`，`quant_method=modelopt`；
- MTP 层为 BF16，且 PLE 的 `weight_scale=0.0002`。

准备脚本会检查 PLE 表大小必须为：

```text
51,200,245,760 bytes
```

## 3. SGLang 镜像

实测镜像 tag：

```text
flashnext-base:090c06d563692003
```

它基于 SGLang commit `aa5551d9b6ebe133427a8a0b368c2643f79d5440` 的定制构建，包含：

- `0.0.0.dev1+gaa5551d9b`
- PyTorch `2.15.0.dev20260818+cu134`
- Transformers `5.19.0`
- FlashInfer `0.7.0.post1`
- ModelOpt `0.47.0`

**私有镜像不包含在本仓库中。** 复现时设置：

```bash
export IMAGE="<your-sglang-image>"
```

如果镜像内的 SGLang 版本或 QSA/Mamba 内核不同，容量、TTFT 和回放结果不能直接对比。

## 4. 运行目录

```text
$PROJECT_ROOT/
├── checkpoints/<checkpoint_id>/ple/         固定 PLE 表
├── checkpoints/<checkpoint_id>/model-reuse/ 主路径硬链接视图
├── runs/<RUN>/cache/                        该 RUN 的 HiCache 文件
├── runs/<RUN>/logs/                         启动日志
├── runs/<RUN>/results/                      配置、资源、请求、质量结果
└── serial.lock                              单 RUN 串行锁
```

PLE 目录按 checkpoint 固定，多个 RUN 串行复用；KV、日志和结果按 RUN 分开。

## 5. 安全与资源预留

- 单实例、最多 2 并发。
- 容器 `--memory 98g --memory-swap 98g`。
- 启动前至少 16 GiB MemAvailable；建议 20–24 GiB。
- 运行中最低 MemAvailable 低于 18 GiB 应停止。
- 不要在唯一生产机上无人值守运行新的 FS/CPU 分层实验。
- 新分层实验先把容器降到 88–90 GiB，并加 watchdog。

## 6. 不包含的内容

- 模型权重和 PLE 大表
- 私有 SGLang 镜像
- 运行缓存、HiCacheFile 数据
- 任何密码、token、私钥或内部地址
- 四机实测数据

## 7. 已知限制

- 四台 Spark 的组合建议只是线性估算，不是实测。
- 1M 上下文未测；262k 已实测。
- FP8 权重未测；不能用本仓库的 NVFP4 结果回答 FP8 权重问题。
- FP4 KV、QSA FP8 indexer、unified memory、NGRAM 在当前模型/后端不支持。
- R054 未独立重跑 200 题 GSM8K；176/200 继承 R033，原因是同权重、同 KV、同计算路径。