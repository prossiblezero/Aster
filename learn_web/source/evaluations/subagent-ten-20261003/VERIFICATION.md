# 工程与证据验证

这些检查验证实现和测量的可靠性，不替代真实模型对照。r0–r5 共 200 回合全部完成；最终 r5 严格通过 Solo 13/20、team 15/20，19 对完整用量总量比 1.173380，达到原整题通过率与适度成本分支。全体行为事实略降及一次预算失败保留，结论范围见 REPORT.md。

## 隔离重建

起点是 `ef471322d2291fe253e152a6efa5bb389bcf0ca0`，应用本轮开始时保存的工作区补丁，并恢复保存的初始源码、测试和必要评测资料，再应用本轮候选。这样保留用户已有未提交工作，同时避免验证依赖当前工作区中的额外修改。没有复制 `.env`。

| 阶段 | 独立目录 | 结果 |
| --- | --- | --- |
| r2 | `/tmp/aster-ten-verify-x1d57b5j` | 1,129 passed，4 skipped；55 模块与冻结 r2 一致 |
| r3 | `/tmp/aster-ten-verify-r3-bm_dxt_8` | 1,138 passed，4 skipped；55 模块与冻结 r3 一致 |
| r4 | `/tmp/aster-ten-verify-r4-6tji7bk7` | 1,138 passed，4 skipped；55 模块与主区候选一致 |

完整命令为 `PYTHONPATH=<独立目录>/src python -m pytest -q`。r3 完整回归后仅进一步收紧旧评测用量完整性门控并扩展该回归断言，根工作区和独立目录的 `tests/test_subagent_evaluation.py` 均再跑 14 passed。生产源码未因此改变。

独立目录及根工作区的 `python -m ruff check src tests scripts learn_web/scripts learn_web/tests` 通过。文档更新后，两处重新生成的网站共 116 个镜像/索引文件逐字节一致，站点检查 6 passed，`git diff --check` 通过。

日志与逐项哈希位于私有目录的 `isolated-pytest.log`、`isolated-r3-pytest.log`、`isolation.json`、`isolation-r3.json`。r3 生产补丁摘要为 `05f08f3e07386049ddfdf030ace412bbca47eee1d42dffc41bc17d651ba90e31`。

r4 初版完整回归不是全绿：13 项缺少独立目录的历史报告夹具，1 项在 chat 4K 上下文中必需请求超出预算。复制原三份夹具并记录哈希，压缩新增通用提示而不提高预算，27 项受影响回归和完整 1,138 项最终通过；全范围 ruff 通过。证据见 `isolated-r4-pytest.log`、`isolated-r4-final-pytest.log`、`r4-fixture-restoration.json` 和 `r4-isolation-preparation.json`。

r4 文档与源码镜像重新生成后，主区和独立目录的 116 个生成产物逐字节一致，独立目录站点检查 6 passed，`git diff --check` 通过；哈希记录在 `r4-site-hashes.json`。这些文件未发布到网站。

## 安装后的真实路径

在独立目录用 `python -m pip wheel --no-deps . --wheel-dir <私有产物目录>` 构建，并以 `pip install --no-deps --target <新临时目录>` 安装；未修改评测使用的 Python 包环境。首次无构建隔离方式因缺少 hatchling 失败，随后标准隔离构建成功，属于构建环境问题。

r3 wheel：`aster_harness-0.1.0-py3-none-any.whl`，SHA-256 `d3f19180258280802cdd261693035fa56132a669e584c18d8b76fce6caf58f5a`。归档中的 55 个模块逐字节等于候选源码，没有 `.env`、字节码、缓存或日志。

从 `/tmp` 使用安装路径运行 `aster --help` 成功。随后确认实际 import 来自安装目录，以本地固定模型完成真实父子 Runtime 路径：孩子读取文件、完成交付，父方通过 status 接收源码观察并完成任务；已暴露工具不在加载目录，all 配置下隐藏加载器。该检查不调用模型服务。记录见 `wheel-r3-install.json`、`wheel-r3-help.txt` 和 `wheel-r3-build.log`。

r4 使用相同隔离构建与安装方式，wheel SHA-256 为 `270ccb25e5606e858e49b1c1f3ae8295a74132f112ce994f23f59ccd9ad3e506`。归档 55 模块与冻结 r4 一致，无环境文件和缓存噪声。安装目录 `/tmp/aster-ten-r4-installed-uxitssrk` 的 CLI 帮助成功，确认实际 import 路径后，使用已安装包运行自动交付、单个/批量 status、来源选择及快照恢复共 5 项既有真实 Runtime 测试，全部通过、零模型服务请求。记录见 `wheel-r4-install.json`、`wheel-r4-runtime.log` 和 `wheel-r4-help.txt`。

## 回归与测量审计

- 8,000 字符交付覆盖旧上限以上的完整证据，原有越界、来源归属、陈旧证据和权限拒绝继续检查。
- 已有子会话的 status 可见性覆盖恢复和历史分支；加载目录新行为在旧实现上有两项真实失败，修正后工具子集 104 passed，扩大相关范围后 231 passed。
- 驱动 14 项回归覆盖未知用量、HTTP 尝试计数、畸形引用、坏结果、错轮次、旧结果混入新轮、冻结中断及并发原子写入。失败和修复后输出均保存。
- 80 个行为事实及 90 个评分负例在各轮冻结前通过。新源码缓存另在空目录真实下载过，按固定摘要验证并保存许可证。
- r0/r1/r2 全 80 回合的最终成绩复算不变。r2 完整请求用量与账本相等，源码和工作区保持原样，事件哈希链完整，顺序符合预登记。离线分析脚本为 `analyze.py`。
- `compare.py` 在全部预定回合完成并运行 `analyze.py` 后生成分遍、合并、完整用量与可解析答案的配对汇总，以及包含真实等待参数的 `WAIT-BATCHES.json`。以 r3 原结果作复算，全部数值字段一致，未覆盖历史文件；记录见私有 `compare-reproduction.json`。它不修改评测驱动或评分器。
- 审查发现空/缺项 TRACE-HASHES 清单可被误判完整、零 token 配对可能除零，已修复并保留四项修前失败；修后 18 项评测回归通过。清单必须精确覆盖全部预定结果、launch、账本与 trace；零分母不参与比率计算，配对数量与总量仍保留。见 `compare-before.log`、`compare-after.log`。

## 独立审查范围

| 范围 | 状态与证据 | 未覆盖 |
| --- | --- | --- |
| 评测公平性、假设与身份 | 完成；完整性缺陷已有失败回归并修复，旧三轮九份结果/汇总/配对一致 | 未模拟实际断电或磁盘故障 |
| 架构、组合和级联 | 完成；384 组工具目录组合及能力切换探针通过，未发现剩余实质问题 | 提示遵循率须真实模型验证 |
| 安全与 abuse cases | 完成；执行端权限、来源信任、工具隐藏及模型与评分文件边界复核 | 不是任意恶意代码的完整安全证明 |

审查者没有调用模型服务或读取 `.env`。r3 首个真实请求与独立健康检查均返回 401，失败被保留。北京时间 2026-10-03 16:47 健康检查成功并通过冻结校验，从下一回合续跑；第 35 回合另一次 provider_error 后健康检查成功，再从第 36 回合继续。两次恢复均未替换失败。全部 40 回合现已完成；原始结果重评分、用量账本、工作区、事件链和预定顺序审计通过，见私有 `r3/offline-audit-final.log` 及公开 `r3/TRACE-HASHES.json`。没有提交、推送或对外发布。

r3 完成后另有只读专项复核：40 个结果、55 个源码文件、17 个归档驱动文件、180 个 artifact 哈希、60 条 trace 的 42,510 个事件，以及 236 次请求用量逐项一致；summary/pairs/comparisons/diagnostics 独立重算一致。20 份交付与成功 worker_finish 对齐，80 个事实正确。OpenLibrary 的异常名称差异仅作敏感性说明，未修改正式评分；即使视为等价，成本目标仍未达到。

## r5 候选验证

独立目录 `/tmp/aster-ten-verify-r5-oemzo654` 从已验证 r4 重建，起始 55 模块逐字节一致，未复制 `.env`。候选仅改变已启用 `worker_start` 的直接展示及重复说明；初次完整回归 1,142 passed、4 skipped、1 项旧可见性断言失败。更新该约定后 180 项相关检查通过。一次定向命令漏设 PYTHONPATH，误导入主区 r4 而失败；日志保留，纠正导入后通过，不把它计作候选产品回归。

专项审查发现启动 schema 使原本可用的 4K 请求溢出。真实 Runtime 两个用例修前均失败，精简说明后扩展到三协议 × Memory 开关 × 两种只读/输出配置，12 个组合全部通过。全套回归在第一次精简稿通过 **1,146 passed、4 skipped**；最后只继续缩短启动工具及字段的重复文字，并扩展上述矩阵，受影响的上下文/可见性/父子路径 **179 passed**。全范围 ruff 通过。Chat 最紧组合仅余 7 token，长任务仍受正常上下文容量限制，没有提高预算或保证任意输入都适用 4K。

评测审查另发现旧任务提示禁止自主派工。adaptive 的两臂现统一用中性分工说明，Runtime 请求与 REQUEST.md 一致，旧策略保持原文。新增逐臂身份和提示回归后 `tests/test_subagent_evaluation.py` **20 passed**。复核确认 collect 会计入旧子运行和续问运行；同题多交付明确标记无法唯一归因，另列交付次数与去重题项数。审查范围包括预算、恢复、失败和取消记账，没有调用模型。

最终 wheel SHA-256 `dc15cb2b31efeb44709ad00455947d79aab4c109c9a39da5704c1aae64a3e6fb`；55 模块与候选逐字节一致，无 `.env` 或缓存噪声。安装至 `/tmp/aster-ten-r5-installed-kkm52ja0`，确认实际 import 来自安装路径，CLI help 成功；使用安装包跑交付、取证、并发槽复用和 4K 矩阵共 **18 passed**，零模型服务请求。见 `wheel-r5-install.json` 与 `wheel-r5-runtime.log`。

应用前确认主区生产源码仍等于冻结 r4，只应用已审的 10 个生产/测试/驱动文件增量，记录 `r5-application.json`。相对本轮 r0 的累计生产补丁为五文件 +51/-37，SHA-256 `e9c488c3f60c25d19d5c6a797dcc31b341ae856dc68dee2590675e9d7221d4f5`。r5 冻结摘要 `8e47a2e26bde5a76c5d63419d41da90c367a856fa75ce1b16f54950805bb6ec8`，预检 80 事实、90 负例通过；与 r4 逐项核对全部 oracle 和源码哈希一致，仅 REQUEST.md 分工限制改变。两臂配置只有 max_workers 不同。真实 40 回合现已全部完成，保留全部首次结果。

r5 主区与独立目录生成的 116 个网站产物逐字节一致，站点 6 passed，`git diff --check` 通过，见 `r5-site-hashes.json` 与 `r5-site-tests.log`。没有提交、推送或发布网站。

## r4 完成后的独立审计

40 个结果及 launch、55 源码、17 归档驱动、180 个 artifact SHA、60 条 trace 的 49,060 个事件、243 次逻辑请求与 245 次 HTTP 尝试逐项核对通过。20 份成功 worker_finish 的答案及 73 段源码摘录与交付对齐。两次缺失用量均是父方请求的 transport 重试，最终请求成功；成本配对保留不完整标记。

80/80 只表示子交付的行为值与审查分类正确，不包含引用正确性。Cachetools team/2 的 c1/c2 拼接 symbol 已存在于子交付，父答案逐字保留，属于子引用错误未被父修正。Django review team/1 的最终 JSON 在 observed 值中缺少闭合括号。主成绩与评分标准未改变，归因文字已纠正。

## r5 完整结果复算

`python evaluations/subagent-ten-20261003/analyze.py r5`、`python evaluations/subagent-ten-20261003/compare.py r5` 均退出 0，无模型请求。40 个结果已按冻结顺序完成；离线重评分、账本与供应商已报告用量、工作区完整性、事件哈希链、归档依赖及 artifact 清单一致。主区 55 个生产模块与冻结 r5 一致，17 份 live 及归档驱动依赖一致。见私有 `r5/offline-audit-final.log`、`r5/comparison-final.log` 与公开 `r5/TRACE-HASHES.json`。

唯一缺 usage 是 More Itertools Solo/1 的一次 transport 重试，最终请求成功；因此其成本配对未知，未用零代替。Boltons team/1 的预算失败计入主成绩；两次 More Itertools 成功交付共 8 个正确事实被保留。严格整题两遍各提高一题，完整用量总量比 1.173380、中位比 1.041047；全体行为事实 151/160 → 148/160，双方可解析配对 143/152 → 148/152。指标与原停止条件保持一致。

独立最终审计通过，见 `r5/independent-audit.py` 与 `r5/independent-audit.json`。覆盖 40 回合、55 源码、17 份驱动依赖、163 份 artifact 清单文件、43 条运行轨迹的 **32,557 个事件**、179 次逻辑请求、180 次 HTTP 尝试及 9 段交付源码证据（8 段成功、1 段部分交付）。冻结身份、配置、工作区、每条事件哈希链、ledger/checkpoint/result 用量、工具结果、最终答案及父方实际收到的交付逐项一致；summary、pairs、comparisons、diagnostics、WAIT-BATCHES 均独立复算一致。

两份成功子报告各严格 4/4，八个行为值全部进入父答案。More Itertools/1 的 m2 引用从子方 1078 行改为父方 1068 行，两者均在 `windowed` 的有效范围，不能说所有引用逐字保留。主代理一次临时探针错误地要求引用完全相等而失败，定位后改为分别验证两个引用并检查行为值保留，八项通过；该探针未改变正式评分。证据见 `r5/main-contribution-check.json`。

Boltons 父方下一次请求估计为 44,942 输入加 8,192 输出，276,588 + 53,134 超过 300,000 团队上限；停止原因与源码、轨迹一致。该失败不阻止原整题通过率分支达标，但明确限制全体事实准确率和交付可靠性结论。

最终文档的 129 个本地链接目标存在；更新学习章节后，主区和独立目录的 **118 个生成产物逐字节一致**，静态站点检查 **6 passed**，`git diff --check` 通过。见 `r5-final-doc-sync.json`、`r5-final-site-hashes.json`、`r5-final-site-tests.log`；源码仍与冻结 r5 一致，零新增模型请求，没有提交、推送或发布。

## r6 工程验证

用户继续优化后，从已验证 r5 复制到 `/tmp/aster-budget-r6-o98knuym`，未复制 `.env`，主区生产源码仍保持 r5。候选只改 Runtime 和 WorkerManager，相对 r5 **+21/-10，净增 11 行**；没有运行依赖、公开配置、工具或持久状态变化。补丁为 [r6-candidate.patch](r6-candidate.patch)，SHA-256 `52896763f1f7c52c19bfa346429ffeabbffd54872d7e231106bbd2a047213be9`。

真实 Runtime 回归覆盖读文件导致当前请求增大、过时估计、未知 usage 保守记账、无工具响应仍试图调用工具、充足或无限预算；新三项 token 边界用例在原 r5 均失败，候选通过。审查调用链另发现初稿在本轮达到团队工具额度后会跳过下一次总结，顺序/并行两个失败用例已保留并修复；旧局部额度、调用上限、并发预留及恢复检查继续通过。

相关检查 **113 passed**。第一次完整检查 1,150 passed、4 skipped、13 failed，失败均为隔离目录缺少三份原有报告夹具；按已记录摘要复制原夹具后完整检查 **1,163 passed、4 skipped**。全范围 ruff 通过。日志保存在 `artifacts/private/subagent-budget-20261004/`，包含修前失败、修后检查及夹具哈希，不将环境缺件归为生产回归。

隔离 wheel 的 55 个模块与候选逐字节一致，没有环境文件、缓存或日志；wheel SHA-256 `ec2e6258e6834a66faf611d48970851664ad49418f2a60fe8eb11f7cf210685e`。安装到 `/tmp/aster-budget-r6-installed-t6lmugo_` 后确认实际导入来源、CLI 帮助通过；用已安装包执行新 token 路径、旧 partial、工具拒绝和团队预算共 **15 项检查通过**，零模型服务调用。以上仅证明工程路径，不证明真实模型效果。

Standard 架构专项审查完成，无剩余有证据问题。独立离线探针三协议共 **9 组通过**，覆盖并发在途预留、重建后实际请求估算与落盘一致性、已知/未知用量、交付去重和 partial 续跑；续跑恢复工具，旧交付快照保持一致。探针及结果见私有 `r6-architecture-probe.py`、`r6-architecture-data-3cijg9yn/result.json`。未把启发式当成未来预算可用性的保证。

r6 冻结 SHA-256 为 `535945ec18a37ffd29c54e1ffb71c99da66bd8a1c104d76d74093c90969b5a62`，80 事实及 90 负例预检通过。与 r5 的模型、协议、配置、endpoint 摘要、环境、任务、oracle、请求文本和执行顺序完全相同；17 项驱动依赖只新增了 PROTOCOL 的 r6 预登记。40 回合完整对照使用冻结候选源码，主区继续保留 r5。

全部 40 回合已完成，驱动 exit 0。原有 analyze/compare 离线检查通过：40 个预定结果与身份、55 模块、17 归档依赖、160 份 trace 清单、原评分重算、请求账本、工作区以及每条事件哈希链均通过校验。全部用量完整，严格通过 Solo 11/20、team 13/20，完整结果见 REPORT.md，不重跑替换任何失败。

另有只读预算路径核验 `r6-budget-audit.py`（exit 0）：每个工作区的所有落盘运行与 result 完全对应，共 **40 个运行、28,283 个事件、148 次模型回复**；20 个 team 初始真实请求均包含 worker_start，实际调用 **0 次**，子运行、子方无工具请求、预算停止和空答案均为 **0**。公开摘要为 [r6/budget-paths.json](r6/budget-paths.json)。新机制未被实际使用，按预登记不采用；生产补丁和测试补丁单独保存，不把候选测试应用到保留 r5 的主区。

收尾确认主区 55 模块逐字节等于 r5，冻结 r6 的 55 模块及 17 项活动驱动依赖未变。143 个文档本地链接目标存在；隔离生成的 118 个网站文件与主区一致，仅四个文档派生文件需要更新。静态站点检查 **6 passed**，主区全范围 ruff 与 `git diff --check` 通过。最终验证记录作为 Markdown 原文镜像同步，不改 HTML 或产品源码；没有提交、推送或发布。

## 2026-10-05 受控续测与采用

随后完成 44 回合真实委派对照，压力分支实际命中六次，按新协议采用 r6。主区 55 个模块等于冻结候选，独立完整回归 1,163 passed、4 skipped。新评测的冻结、全部首次失败、交付和预算边界见[验证记录](../subagent-budget-controlled-20261004/VERIFICATION.md)与[结果报告](../subagent-budget-controlled-20261004/REPORT.md)。以上旧轮未采用结论保留为当时记录。
