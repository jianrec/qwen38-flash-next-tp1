# 指标定义与计算口径

## 有效 token 容量

以下全部针对一个 TP1 实例，不乘设备数或 TP 数。

- **实例 KV 池总容量** $T_{pool}$：采用 `server_info.max_total_num_tokens`。例如 R019 为 607,808 tokens。
- **单请求上限**：

$$T_{single}=\min(T_{pool},L_{max})$$

其中 $L_{max}$ 是服务的 `context_length`。R019 为 262,144，因此 $T_{single}=262,144$，不是 607,808。

- **双并发平均可常驻容量**：

$$T_{avg@2}=\min\left(L_{max},\left\lfloor\frac{T_{pool}}{2}\right\rfloor\right)$$

该值只用于容量折算，不表示框架严格均分。R019 的 $T_{pool}/2=303,904$，大于 $L_{max}=262,144$，所以 $T_{avg@2}=262,144$。

- **每 token KV 字节换算**：

$$b_{token}=\frac{M_{KV}\times 2^{30}}{T_{pool}}$$

其中 $M_{KV}$ 取 `server_info.internal_states[0].memory_usage.kvcache`。这是框架 KV 记录的换算值，不冒充纯 K/V 张量公式；启动日志中的 K/V 实际分配另列。

## 回放覆盖率

对每个目标分别计算，不只给总体比例。

- **完整缓存覆盖率**：

$$C_{full}=\frac{cached\_tokens}{prompt\_tokens}$$

与 `full_replay_cache_required_and_observed` 一致，逐目标要求至少 95%。`cached_tokens` 是实际命中的可复用前缀 token 数，device/host/storage 分层读数同时列出。

- **指定 storage 覆盖率**：

$$C_{storage}=\frac{storage\_tokens}{prompt\_tokens}$$

与 `storage_qualified` 一致。它只计算指定后端（本实验为 HiCacheFile）恢复的 token，不包括 device 或 host 命中。因此 device 命中较多时，$C_{storage}<95\%$ 并不意味着完整回放失败。

- **总体覆盖率**：分子为四个目标命中 token 之和，分母为四个目标 prompt token 之和；不是四个百分比的简单平均。

每个 RUN 的 `coverage.json` / `coverage.csv` 给出四个目标各自的 prompt、device、host、storage、$C_{full}$、$C_{storage}$ 和总体值。`coverage_report.py` 从原始 `cache_pressure/report.json` 生成，不修改原始结果。

## 速度与吞吐

- **TTFT**：发出请求到收到首个输出 token 的时间，报告中位数和范围。
- **单请求输出时间**：首 token 到末 token 的时间，即 `elapsed_s - ttft_s`。
- **单请求 decode token/s**：`1 / TPOT`；同时保留端到端 `completion_tokens / elapsed_s`，两者不混用。
- **实例输出吞吐**：该轮所有正式请求的输出 token 总数除以真实墙钟时间。
- **实例请求吞吐**：成功请求数除以真实墙钟时间。
- **双并发冷请求**：每轮 10 对不同前缀，固定约 8k 输入、256 输出；预热 2 请求不计入，三个独立服务轮次分别保存，最后报告中位数和范围。

## 稳定性

记录 OOM、请求失败、错答、未解析/截断、最低 MemAvailable、Swap 增长、缓存淘汰/重算，以及持续运行时长。开始与结束阶段的性能分开比较，不能用启动 smoke 代替持续稳定性。
