# 通用编码 50 题（2026-10-12）

从四个公开 benchmark 构建的固定混合集，用于 Aster、pi、Codex 的同模型、单次尝试比较。包含旧 20 题和本项目新选的 30 题；旧题已参与开发，必须分组报告。不是各来源的完整官方榜单。

| 来源 | 数量 | 内容 |
|---|---:|---|
| SWE-bench Verified | 20 | 十个 Python 仓库的真实问题修复 |
| Terminal-Bench archive | 8 | C/C++、异步、解释器、迁移、服务、PTY、正则程序 |
| Aider Polyglot | 12 | Python、JavaScript、C++ 各四道实现题 |
| BigCodeBench v0.1.4 | 10 | 数据处理、绘图、文件与标准库组合 |

新增仓库题按专家耗时分层、固定哈希排序；Aider 按语言配额固定哈希抽样；BigCodeBench 限定可离线运行的 CPU 依赖后固定哈希抽样；终端新题按任务类型覆盖。选题不考虑是否适合拆给子 Agent，也不以任何 Agent 的通过率作为依据。预检中无法评分的候选及替换原因保留在选择记录中。

[任务元数据](tasks.json) · [选题过程](selection.json) · [来源与版本](sources.json) · [镜像](images.json) · [运行协议](../../evaluations/general-coding-50-20261012/PROTOCOL.md) · [结果报告](../../evaluations/general-coding-50-20261012/REPORT.md)

每题先运行初始代码和参考解，核对测试数量；模型看到原题和起始工作区。SWE 和 BigCodeBench 隐藏测试在答题结束后注入，Aider 提供原公开测试但评分时恢复受保护副本。原始材料、参考答案和完整轨迹存于控制端 `artifacts/private/general-coding-50-20261012/`，不挂载给 Agent。

三者每题一次，共 150 次；同模型、同时间及资源预算。所有供应商故障、超时、缺失用量和评分错误保留，不将旧成绩或择优重跑拼入本轮。整题等权，分别报告总体、来源和旧题/新题的通过率、token、请求次数及耗时。

这是本地环境适配结果：使用上游原始测试断言，调整解释器、离线依赖和测试启动方式。JavaScript 启用 `xtest`/`xit` 必做测试，明确可选的 `test.skip` 保留；C++ 启用全部必做测试；SWE 修复测试路径还原的执行顺序。详见协议。

Terminal-Bench、BigCodeBench 原许可证为 Apache-2.0；Aider 练习保留各 Exercism track 的许可证与作者；SWE harness 的 MIT 不替代各任务仓库许可证。原 canary 和来源文件保留。任务仅作评测，不用于训练。
