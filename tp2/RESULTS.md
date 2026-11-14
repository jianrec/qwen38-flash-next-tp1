# TP2 结果摘要

| 组 | 配置变化 | 正确性 / SLO | TTFT | TPOT | 输出吞吐 | SLO goodput |
|---|---|---:|---:|---:|---:|---:|
| E089 | `MAXREQ=3`，Host4，direct | 75/75，8/8 | — | — | 24.434 tok/s | 1.527 req/s |
| E090–E092 | E089 三次独立性能重复 | 9/9 重复正确 | 0.296 s | 64.32 ms | 24.86 tok/s | 1.554 req/s |
| E093 | Host4→3 GiB | 75/75，7/8 SLO | — | — | 21.491 tok/s | 1.175 req/s |
| E094 | direct→kernel，功能组 | 75/75，8/8 | — | — | 25.135 tok/s | 1.571 req/s |
| E095–E097 | kernel 三次独立性能重复 | 9/9 重复正确 | 0.322 s | 64.14 ms | 21.87 tok/s | 1.321 req/s |

E093 释放了约 1–2 GiB UMA，但 SLO 回退。E094 单轮功能组表现较好，E095–E097 的独立性能重复低于 direct 路径，因此最终采用 `direct + page_first_direct`。

E089 功能组的每目标完整缓存覆盖约 99.5%，其中 Host 恢复 4,096 tokens，HiCacheFile 回读 1,600 tokens。文件层单独覆盖约 28%，不能把 Host 命中写成 SSD-only 命中。

原始数据在 [`results/`](results/) 和 [`logs/`](logs/)。主配置在 [`configs/best.env`](configs/best.env)。
