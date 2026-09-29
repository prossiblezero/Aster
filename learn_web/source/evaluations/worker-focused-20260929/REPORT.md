# Worker 聚焦复验报告（2026-09-29）

本轮用于回答一个具体问题：在已经修复请求传递、部分交付和原生测试证据之后，Worker 委派是否能在多步骤代码任务中带来可重复的任务收益。评测已经完成，不再追加模型轮次或更换题目。

## 结论

Worker 在这组任务上确实出现了正向案例，但没有形成稳定的整体收益证据：冻结独立评分从 Solo 的 **2/12** 变为 Worker 的 **5/12**，正确且正常交付从 **1/12** 变为 **3/12**；同时全队报告 token 增加 **21.6%**，运行时间增加 **30.2%**。按配对评分为 Worker **3 胜、0 负、9 平**，但两臂都通过的配对只有 2 对，且这些配对 Worker 仍多用 **25.2%** token、慢 **32.1%**。两臂都“通过且正确正常交付”的配对子集为空。

因此，本轮支持的工程表述是：Aster 能保存并投递失败、取消和额度耗尽时的 partial handoff，并在特定的可切分代码任务中完成一次有实际写入的协作；本轮不支持“Worker 普遍提高通过率”“降低成本”或“加速”。Worker 可以作为默认关闭的可选能力保留，简历中不应把 2/12→5/12 脱离题目复用、有限评分器和额外成本单独包装成稳定收益。

## 评测协议

- 固定 4 道此前见过的 Python 仓库题：Django related managers、SymPy 三种 printer、OpenLibrary authors、SymPy 十二种 CDF。两道 medium、两道 hard；它们是 development reuse，不是 holdout，也没有简单题。
- 每题 3 次重复，比较 `max_workers=0` 的 Solo 和 `max_workers=2` 的 Worker，共 **24 个 episode、12 个配对**。所有运行串行、使用新进程和新工作区，6 对由 Solo 先开始、6 对由 Worker 先开始。
- 两臂绑定同一份 Aster 源码 `856345e`，模型、端点、TaskSpec、外部团队上限、测试依赖和评分器一致；Worker 没有额外预算或廉价子模型。真实响应模型为 `gpt-5.6-luna`。
- 原评分、所有失败和预算停止均保留。评分是 native-equivalent 改编评分，不是 SWE-bench 官方榜单成绩；任务选择、哈希和预算见 [PROTOCOL.md](PROTOCOL.md) 与 [数据选择](../../datasets/worker-focused-20260929/README.md)。

## 汇总结果

| 指标（12 次/臂） | Solo | Worker |
| --- | ---: | ---: |
| 冻结独立评分通过 | 2/12 | 5/12 |
| 正确正常交付 | 1/12 | 3/12 |
| 报告总 token | 7,355,188 | 8,940,292 |
| 累计运行秒数（不含评分） | 4,315.8 | 5,620.5 |
| 模型调用 | 275 | 413 |
| 工具调用 | 613 | 849 |
| 协调调用 | 0 | 26 |
| 子任务启动/正常完成 | 0/0 | 14/2 |

Worker 的模型调用多 50.2%，工具调用多 38.5%。696 次 HTTP 尝试中有 8 次传输失败没有 usage（Solo 2、Worker 6），不能把这些请求当作免费，也没有端点价格，因此报告只使用供应商已报告 token，不编造美元成本。所有 24 条结果均保留；完整账本、源码和输入一致性审计见 [RESULTS-AUDIT.md](RESULTS-AUDIT.md)。

分题结果如下。`正确正常` 同时要求独立评分通过、Runtime completed、stop reason 为 verified 且没有 episode 错误。

| 任务 | Solo 通过 / 正确正常 | Worker 通过 / 正确正常 | Worker token变化 | Worker时间变化 |
| --- | ---: | ---: | ---: | ---: |
| Django-16256 | 0/3 · 0/3 | 0/3 · 0/3 | +59.93% | +63.05% |
| SymPy-14248 printing | 0/3 · 0/3 | 0/3 · 0/3 | -1.68% | +16.64% |
| OpenLibrary authors | 1/3 · 1/3 | 2/3 · 1/3 | +67.40% | +51.46% |
| SymPy-13878 CDF | 1/3 · 0/3 | 3/3 · 2/3 | +1.39% | +10.07% |

printing 的 token 下降发生在双方都因预算停止而失败的重复中，不能当作有效效率收益。CDF 的原评分只包含 Arcsin 加 19 个回归项，6 条 CDF episode 都有非评分失败；因此 Worker 3/3 不能写成十二种分布全部正确。

## 子任务实际贡献

审计没有把“启动过子任务”“收到报告”或 `completed` 自动算成贡献，而是追踪了分工、子动作、交付状态、父请求中的使用和最终修改。

- **CDF repeat-01 Worker** 是最清楚的正面例子。唯一写入的子任务实际实现了 Arcsin、Dagum、Frechet、Kumaraswamy、Laplace、Logistic 六个 CDF 方法；父任务随后验证并保留这些修改。公开补充诊断首轮 Worker 的 12/12 内部样例通过，而 Solo 为 10/12。
- 这个正面例子没有在后两轮稳定复现：三轮公开 CDF 诊断合计 Solo **30/36**、Worker **31/36**；Worker 仍有 Erlang 精度问题以及 StudentT/UniformSum 未完成或超时。详见 [PUBLIC-DIAGNOSTICS.md](PUBLIC-DIAGNOSTICS.md) 和 [CDF-AUDIT.md](CDF-AUDIT.md)。
- **OpenLibrary** 的子任务完成调查并送达父任务，repeat-03 子任务还实际完成了 105 项修改前基线测试；但没有证据证明子任务提供了促成正确补丁的独有关键发现，父任务仍承担主要实现和语义判断。详细证据见 [OPENLIBRARY-AUDIT.md](OPENLIBRARY-AUDIT.md)。
- **Django** 的子报告进入父上下文，但父子大量重读相同来源，三轮均遗漏 GenericRelation；部分报告还带来错误测试标签，增加了收集失败。详见 [DJANGO-AUDIT.md](DJANGO-AUDIT.md)。
- **printing** 的子任务没有形成可确认的独有修复；三轮整体失败，后续公共样例只保留少量 `unknown`/单点匹配，不把格式差异强行标为错误。详见 [PRINTING-AUDIT.md](PRINTING-AUDIT.md)。

原评分与这些需求级诊断保持分离：诊断用于解释覆盖和贡献，不替换独立任务分数。

## 补充诊断与边界

补充脚本是 stdlib-only、无模型请求的公开样例检查，运行在已完成的候选工作区中。打印器每个样例只检查 `A - A*B - B`；CDF 每个分布只检查三个点、概率范围、未求积积分和 Erlang 精确性。它不能证明边界、任意参数、完整符号恒等式或完整测试套件。

原评分还有两项继承限制：SymPy printer 评分器会折叠跨文件同名测试；CDF 评分集只把 Arcsin 作为 F2P。所有未知、超时和非评分失败都保留，没有把缺证据改标为成功。需求边界和预先定义的标签规则见 [REQUIREMENTS-AUDIT.md](REQUIREMENTS-AUDIT.md)。

## 复现与提交内容

`summary.json`、`pairs.json`、`results.json` 是冻结 summarizer 的机器可读结果；`DJANGO-AUDIT.md`、`PRINTING-AUDIT.md`、`OPENLIBRARY-AUDIT.md`、`CDF-AUDIT.md` 是人工轨迹审计；`RESULTS-AUDIT.md` 核对输入、账本和评分。私有完整 trace 位于本地 `artifacts/private/worker-focused-20260929/`，不应上传，也不包含在本提交中。

本轮没有新增生产依赖或生产源文件。修复后的源码指标为 54 个 Python 文件、11,351 行物理代码（比保护基线增加 155 行，约 1.4%，主要是原生测试入口证据修复）。离线工程回归为 **1067 passed / 4 skipped**；Go 适配环境 **4 passed**，wheel 实际从 site-packages 导入，CLI/TUI 启动与恢复验收通过。代码修复和验证提交为 `856345e`，本轮冻结评测提交为 `b0ab873`。
