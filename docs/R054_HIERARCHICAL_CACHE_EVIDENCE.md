# R054 能区分前缀淘汰、读回和重算吗？

R054 能确认请求实际复用的前缀来源、HiCacheFile 逻辑预取和尾部 prefill。现有记录只能在服务级确认淘汰与写回，仍缺逐目标淘汰原因、分组件恢复明细及 NVMe 块设备读量。SGLang 公开提交 `aa5551d9b` 与报告的软件版本一致。 [基线](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/RESULTS.md#1-最终配置的实际精度) · [SGLang 固定提交](https://github.com/sgl-project/sglang/commit/aa5551d9b)

| 请求 | 总输入 | 可复用对齐前缀 | Device / Host / File 复用 | 未复用 / 日志 prefill |
| --- | ---: | ---: | ---: | ---: |
| 压力回放目标 0、1、2（各自） | 5,723 | 5,696 | 0 / 4,096 / 1,600 | 27 / 27 |
| 压力回放目标 3 | 5,722 | 5,696 | 0 / 4,096 / 1,600 | 26 / 26 |
| 262k 热请求、热回放（各自） | 261,925 | 261,888 | 261,888 / 0 / 0 | 37 / 37 |

本负载按 64-token 页对齐，匹配上限预留末 token。压力回放覆盖对齐前缀的 100%，合计覆盖总输入约 99.53%，File 单层约 27.96%。脚本的“完整回放”门槛是总输入覆盖至少 95%。日志 `#new-token` 计量了尾部 prefill，未见对齐前缀内的重算。 [压力原始报告](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/cache_pressure/report.json) · [262k 原始报告](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/long_context/report.json) · [验收脚本](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/bench/cache_acceptance.py#L570-L574) · [匹配上限](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/managers/schedule_batch.py#L1714-L1724) · [回放日志](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/logs/launch.log#L992-L1004) · [热请求日志](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/logs/launch.log#L593-L596)

- **查找、写回和淘汰。** `write_back` 在设备淘汰时尝试备份，Host 写完后可写入 File。压力报告累计设备淘汰 146,304 KV tokens，其中有 3,200 tokens 因 Host 压力无备份丢弃。回放时目标设备命中为零，丢弃事件未关联到目标前缀。 [压力指标](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/cache_pressure/report.json) · [淘汰及写回源码](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/unified_radix_cache.py#L806-L989)
- **文件预取和物理读。** 四次预取各完成 5,696 tokens，拼接时发现其中 4,096 已匹配，最终将新增的 1,600 归入 File。`direct` 配置用于 Host/Device 搬运，文件读取走 `readinto`，可能命中系统页缓存。块设备读量未知。 [回放日志](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/logs/launch.log#L992-L1004) · [预取归账](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/unified_radix_cache.py#L2322-L2394) · [传输配置](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/managers/cache_controller.py#L885-L903) · [文件读取](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/hicache_storage.py#L479-L498)
- **混合缓存。** 主 KV 有逐请求复用和逻辑预取数据。GDN 对应 Mamba 池的状态槽，Host→Device 累计完成 8 槽，缺逐目标快照归属。日志确认 QSA 索引随 KV 注册，固定源码要求随页恢复，聚合 token 指标跳过共享 KV 索引的附属池。逐目标 QSA 恢复来源与数量由实现和正确回放间接支持。 [模型论文](https://arxiv.org/abs/2608.30320) · [启动日志](https://github.com/jianrec/qwen38-flash-next-tp1/blob/ef608a8/results/R054/logs/launch.log#L350-L354) · [Mamba 状态传输](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/unified_cache/components/mamba.py#L756-L825) · [QSA 注册](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/hybrid_cache/hybrid_pool_assembler.py#L1851-L1868) · [指标源码](https://github.com/sgl-project/sglang/blob/aa5551d9b/python/sglang/srt/mem_cache/hybrid_cache/hybrid_cache_controller.py#L923-L945)

首轮对照按请求和前缀范围关联各组件的匹配、逐层淘汰、写回、预取及恢复完成事件，记录原因、页数或状态槽及实际 prefill 区间。同一回放窗口记录 File 进程读取字节和 NVMe 块设备读取字节，并用空闲窗口扣除背景 I/O。
