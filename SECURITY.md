# 安全与数据边界

- 本仓库不包含模型权重、PLE 大表、私有 Docker 镜像、运行缓存或任何凭证。
- 不含密码、SSH 私钥、GitHub token、HF token 或内部 IP。
- 文档中的示例路径使用 `/models`、`/workspace` 或 `$PROJECT_ROOT`，不包含原机器的 home 路径。
- 结果 JSON/CSV 可能包含请求文本的哈希和输出片段；不包含用户隐私数据。
- 如果需要提交自己的结果，请先确认日志中不含 API key、token、内网地址和主机凭证。

## 报告问题

如果发现凭证或敏感信息误入仓库，请立即在 GitHub 仓库开 issue 或联系维护者删除历史记录。仅删除最新 commit 不足以清除 Git 历史中的敏感内容。