# Worker owned requirement 三臂评测报告（2026-09-30）

本轮验证 `a3d3364` 的 Worker 委派与需求归属、部分交付和父任务验收链。评测使用冻结的 10 道代码任务（3 simple、5 medium、2 hard，来自 5 个来源），每题每臂独立运行两次，共 **60 个真实模型 episode**。两次是两个独立的 pass@1 样本，不是 pass@2、best-of-two 或重试。

三组输入、提示、模型、端点、工作区、评分器和全队预算相同：

| 臂 | 源码 | Worker |
| --- | --- | ---: |
| Solo | 新版 `a3d3364` | 关闭 |
| Current | 旧版 `6ccb8be` | 最多 2 个 |
| Worker | 新版 `a3d3364` | 最多 2 个 |

模型为 `gpt-5.6-luna` Responses，64K context、8192 output，strict completion，Memory 关闭。每个任务的模型调用、工具调用和报告 token 由父子共同消耗；simple / medium / hard 的全队报告 token 上限分别为 500K / 750K / 1M。任务、提示、评分器、源码绑定和诊断输入均由 SHA-256 锁定。这是 development-seen regression，不是新的 holdout，也不是论文级 benchmark 结论。

## 结论

新版 Worker 没有在这组任务上提高总体通过率：Solo 为 **14/20 (70%)**，旧版 Current 为 **13/20 (65%)**，新版 Worker 为 **12/20 (60%)**。严格的“独立评分通过 + Runtime completed + verified + 无 episode error”正常正确交付分别为 **12/20、13/20、12/20**。Solo 与新版 Worker 的配对结果为 Worker **0 胜、2 负、18 平**；Current 与新版 Worker 为新版 Worker **0 胜、1 负、19 平**。

新版 Worker 的报告 token 为 **7,431,290**，比 Solo 的 **8,422,505** 少 11.8%；工具调用为 686 对 739，墙钟为 5,067.2 秒对 6,425.3 秒。但新版只启动 3 个孩子，3 个均未正常完成；这 3 个启动所在的 episode 全部失败。因此不能把总量下降归因于委派节省，且共同通过样本上的效率差异也不能证明成功交付降本。新版 Worker 的正确工程结论是：需求归属、部分交付和父验收边界已落地并可审计；真实任务中尚未证明委派提高正确性或稳定降低成本。

## 总体账本

| 臂 | 独立通过 | 正确正常交付 | 报告 total tokens | 墙钟秒 | 模型调用 | 工具调用 | 协调调用 | Worker 启动/完成 | HTTP 无 usage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Solo | 14/20 | 12/20 | 8,422,505 | 6,425.3 | 397 | 739 | 0 | 0/0 | 3 |
| Current | 13/20 | 13/20 | 8,022,002 | 7,196.4 | 444 | 790 | 26 | 14/6 | 6 |
| Worker | 12/20 | 12/20 | 7,431,290 | 5,067.2 | 384 | 686 | 4 | 3/0 | 4 |

未知 usage 请求被保留，未被当成免费调用；没有已确认的端点价格，因此只报告 token。Solo 有 1 次 provider error，Current 有 1 次 TimeoutError；所有评分器均正常返回，没有 grader error。正常交付与独立评分分开统计：例如 Solo 有通过但因 team token stop 而不属于正常交付的 episode。

## 难度和来源

| 难度 | Solo | Current | Worker |
| --- | ---: | ---: | ---: |
| simple（6 条） | 6/6 | 6/6 | 6/6 |
| medium（10 条） | 6/10 | 6/10 | 6/10 |
| hard（4 条） | 2/4 | 1/4 | 0/4 |

| 来源 | Solo | Current | Worker |
| --- | ---: | ---: | ---: |
| Aider polyglot（6） | 6/6 | 6/6 | 6/6 |
| BigCodeBench（2） | 2/2 | 2/2 | 2/2 |
| SWE-bench Pro（2） | 1/2 | 0/2 | 0/2 |
| SWE-bench Verified（6） | 1/6 | 1/6 | 0/6 |
| Terminal-Bench（4） | 4/4 | 4/4 | 4/4 |

任务级结果（括号内为正确正常交付数）如下：

| 任务 | Solo | Current | Worker |
| --- | ---: | ---: | ---: |
| grade-school | 2/2 (2) | 2/2 (2) | 2/2 (2) |
| transpose | 2/2 (2) | 2/2 (2) | 2/2 (2) |
| BigCodeBench-25 | 2/2 (2) | 2/2 (2) | 2/2 (2) |
| Forth | 2/2 (2) | 2/2 (2) | 2/2 (2) |
| Django-16256 | 0/2 (0) | 0/2 (0) | 0/2 (0) |
| SymPy-14248 printing | 0/2 (0) | 0/2 (0) | 0/2 (0) |
| gRPC KV store | 2/2 (1) | 2/2 (2) | 2/2 (2) |
| COBOL modernization | 2/2 (2) | 2/2 (2) | 2/2 (2) |
| OpenLibrary authors | 1/2 (1) | 0/2 (0) | 0/2 (0) |
| SymPy-13878 CDF | 1/2 (0) | 1/2 (1) | 0/2 (0) |

Django 的 6 条 episode 都遗漏 GenericRelation 的三个 async 方法。printing 的 6 条都因预算或实现问题未通过；其余失败、取消和 provider/transport 边界保留在 `results.json` 及私有 trace 中。

## 配对结果与共同成功样本

20 个 task-repeat 配对全部通过输入一致性核对。

| 比较 | 通过：右胜/右负/平 | 正常交付：右胜/右负/平 |
| --- | ---: | ---: |
| Solo → Current | 1 / 2 / 17 | 2 / 1 / 17 |
| Solo → Worker | 0 / 2 / 18 | 1 / 1 / 18 |
| Current → Worker | 0 / 1 / 19 | 0 / 1 / 19 |

Solo 与新版 Worker 共同通过 12 对，合计 token 分别为 2,184,902 与 2,081,966；共同正常交付 11 对，分别为 2,047,260 与 1,814,048。共同样本只是成功条件下的描述性子集，不消除题目难度、模型随机性或剩余工作差异，不能单独推出成本优势。三臂同时通过 12 对，同时正常交付 11 对。

## 委派实际行为

新版 Worker 的 3 个子任务都为部分交付或预算边界，没有一个 child `completed/verified`。其中两次是 SymPy printing 的只读/调查分工，均未写入；一次是 CDF 任务的调查/实现尝试，父任务收到部分信息后自行整合。父检查点记录了交付返回待验收，未把 `delivery=partial` 当作完成。新版 Worker 的 20 条运行中，17 条没有启动孩子；因此大多数新版结果完全是单 Agent 结果。

旧 Current 启动 14 个孩子、完成 6 个。可确认的交付案例包括：

- COBOL 两次 Explore 都完成了固定宽度记录、EOF 和更新语义调查，父任务收到后实现并通过独立评分；父仍需自行整合和验证，不能把这一对单例写成因果收益。
- Forth repeat-02 的 Explore 交付了定义绑定、内置词覆盖和异常边界，父任务引用这些约束并最终通过；父仍在交付后自行修复覆盖分发问题。
- OpenLibrary 和 SymPy printing 的子任务主要是部分调查，父子重复读取来源，未形成独立正确性收益。

这说明“交付进入上下文”与“子任务贡献了正确实现”是两个不同事件。报告中的 Worker 启动、完成、父方消费和最终修改证据均保存在原始 trace；没有把启动或 handoff 数量当作收益指标。

OpenLibrary 的逐条轨迹见 [OPENLIBRARY-AUDIT.md](OPENLIBRARY-AUDIT.md)，SymPy CDF 的委派和需求级诊断见 [CDF-AUDIT.md](CDF-AUDIT.md)；已完成的控制、Django 和 printing 审计分别见 [CONTROLS-AUDIT.md](CONTROLS-AUDIT.md)、[DJANGO-AUDIT.md](DJANGO-AUDIT.md) 和 [PRINTING-AUDIT.md](PRINTING-AUDIT.md)。

## 补充需求级诊断

补充诊断只针对两个已见 SymPy 任务，使用冻结的公共表达式、三个文件区分的 MatrixSymbol 测试、12 个分布的 interior/boundary 点和独立数学参考；它不改变原任务评分，也不增加模型调用。

| 诊断组（两次重复合计） | Solo | Current | Worker |
| --- | ---: | ---: | ---: |
| printer：6 个格式样例 verified / wrong / unknown | 0 / 6 / 0 | 0 / 6 / 0 | 2 / 4 / 0 |
| CDF：144 个固定点 verified / wrong / unknown | 127 / 12 / 5 | 127 / 11 / 6 | 130 / 10 / 4 |

Worker 的两个 printer verified 都是 pretty；str 与 LaTeX 仍 wrong。CDF 的差异主要来自 fixed-point 诊断，Erlang、StudentT、Frechet/UniformSum 的边界或超时问题仍存在。它能说明新版在少数公共表达式上留下了更好的局部结果，不能把局部点数变成完整任务通过率，也不能证明这些结果由子 Agent 产生。

## 验证和可复现文件

- 生产修复提交：`a3d3364 fix: track worker-owned requirements through delivery and review`。
- 评测协议提交：`ac60d29 eval: freeze ten-task three-arm repeated worker comparison`。
- `summarize.py` 输出的机器可读结果：[summary.json](summary.json)、[pairs.json](pairs.json)、[results.json](results.json)。
- 补充协议为 [SUPPLEMENTARY-PROTOCOL.md](SUPPLEMENTARY-PROTOCOL.md)，诊断脚本完成 `audit` 和 `run`；私有逐点输出位于 `artifacts/private/worker-owned-20260930/diagnostics/`，不应提交。
- 全部评测运行在 Conda `aster` Python 3.12，源码绑定和 HTTP ledger 均通过完整性核对。`artifacts/private` 的请求、trace、workspace 和模型输出不纳入版本库。

评测集是开发阶段见过的混合来源样本，Terminal-Bench、SWE 适配器和 SymPy 评分各自继承协议中的限制；它们不能代表官方榜单分数。下一轮若继续优化，应先改善子任务选择、父子工作去重和可执行验收，再冻结新的任务与对照，不应把本轮较短的失败运行写成 Worker 的效率收益。
