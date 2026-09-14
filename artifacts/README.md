# 评测工件

当前结果使用 `benchmark-v2-scripted/` 与 `benchmark-v2-real/` 的 `report.json` 和 `report.md`。报告保留失败；确定性回放不是模型成功率。完整的本机轨迹在对应 `runs/` 中，未提交 Git。

`deterministic-evaluation.*`、`module-benchmark-v1.*` 和 `agent-evaluation-v1.*` 是旧实验记录，不再作为当前简历数字依据。v1 的上下文统计未覆盖完整请求，部分任务数量是同场景重复；早期 token 总量还把输入、输出和 total 相加。不要从旧摘要复制“100%”或旧 token 收益。方法与边界见 [docs/benchmark.md](../docs/benchmark.md)。
