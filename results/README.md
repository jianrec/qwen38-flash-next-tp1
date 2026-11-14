# results/ 证据说明

这些文件来自单机 TP1 的已完成 RUN，已去掉内部地址和 home 路径。

## R054（最终推荐）

| 路径 | 内容 |
|---|---|
| `R054/config.env` | 本次实际启动参数 |
| `R054/server_info.json` | SGLang `server_info`，含 `max_total_num_tokens` 等 |
| `R054/resources.csv` | 每 5 秒 MemAvailable / SwapFree / 容器状态 |
| `R054/cold_concurrent/summary.json` | 冷 8k/256 双并发一轮 |
| `R054/performance_sequential/report.json` | 单请求 shared/unique |
| `R054/performance_concurrent/summary.json` | 热 2 并发三轮 |
| `R054/long_context/report.json` | 262k cold/hot/replay |
| `R054/cache_pressure/report.json` | 480,718-token 压力与回放 |
| `R054/long_quality/report.json` | 20 条 8k/32k 长题 |
| `R054/quality_mmlu/report.json` | MMLU 57 题 |
| `R054/logs/launch.log` | 启动日志 |

`aggregate.json` 是 R054–R056 三轮独立冷启动的聚合结果。

## 基线

- `baselines/R019/server_info.json`：607,808 tokens 基线
- `baselines/R034/server_info.json`：698,560 tokens 上一版
- `baselines/R008`、`R009`：BF16/FP8 自动池直接对照
- `gpuonly-cold/bf16_summary.json`、`fp8_summary.json`：GPU-only 冷请求三轮聚合

## V008（可选 vLLM）

`V008/` 是专用 vLLM + FP4-Local 的对照结果。V008 没有单独的 `server_info.json`；设备 KV 1,120,703 tokens 来自 `logs/launch.log` 的 `GPU KV cache size` 行。

## 读取建议

- 容量以 `server_info.max_total_num_tokens` 或启动日志 `KV Cache is allocated` 为准。
- 不要把 `server_info.internal_states.kvcache` 与启动日志 K+V 混用。
- 压力回放的 storage-only 覆盖率不等于总覆盖率；总覆盖率要用 device+host+storage 合计。
- 单次 RUN 会因启动时可用内存不同产生容量波动；R054 使用三次重启中位数。