# Qwen3.8 Flash Next TP2

这是与仓库根目录 TP1 方案并列的双机 TP2 实验目录。TP1 文件保留在仓库根目录，TP2 只放在 `tp2/`。

## 当前最佳 TP2 配置

- 拓扑：两台 NVIDIA GB10 Spark，zj2 rank0 + zj3 rank1，TP2/DCP1
- 权重：NVFP4 ModelOpt checkpoint
- 主 KV：FP8 E4M3
- QSA indexer：`auto`，保持 BF16 路径
- Mamba SSM：BF16
- Context / token pool：262,144 / 262,144
- `MEMFRAC=0.78`
- `MAXREQ=3`
- Host HiCache：4 GiB
- 存储：本地 HiCacheFile + 本地 NVMe
- 预取：`wait_complete`
- I/O：`direct + page_first_direct`

完整配置见 [`configs/best.env`](configs/best.env)。

## 已验证结果

- E089 功能回放：75/75 正确，8/8 回放达到完整 SLO，完整缓存覆盖约 99.5%，goodput 1.527 req/s。
- E090–E092 三次独立性能重启：双并发热前缀全部正确，TTFT 中位 0.296 s，TPOT 64.32 ms，输出吞吐 24.86 tok/s，SLO goodput 1.554 req/s。
- Host3 GiB 边界组 E093 只有 7/8 达到完整 SLO，保留 Host4 GiB。
- `kernel + page_first` 的 E094–E097 功能正确，但性能低于 direct 路径，不作为默认配置。

当前 checkpoint 是 NVFP4，仓库不把它写成 FP8 权重实测。FP8 KV 的独立容量收益与 TP1 对照保持在根目录 TP1 结果中。

## 目录

- [`configs/`](configs/) TP2 最佳配置和 I/O 对照配置
- [`bench/`](bench/) L3 回放、顺序性能和并发性能脚本
- [`deploy/`](deploy/) TP2 启动入口脚本
- [`results/`](results/) 原始 JSON、CSV、metadata 和实际 server args
- [`logs/`](logs/) 双 rank 日志、资源快照和 benchmark 输出

复现步骤见 [`REPRODUCE.md`](REPRODUCE.md)，结果摘要见 [`RESULTS.md`](RESULTS.md)。
