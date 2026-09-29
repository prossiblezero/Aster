# 24次配对复验：公平性与证据完整性审计

2026-09-29。结论：未发现本轮配对输入、预算、源码或已报告用量的完整性异常；24次结果全部保留，可以按冻结口径报告。但这是4道已见Python开发题的复验，且原评分器覆盖有限，不能视作通用benchmark成功率或完整需求正确率。8次HTTP传输失败未返回usage，不能把它们当作免费调用。

本审计独立读取 `artifacts/private/worker-focused-20260929/` 下的 `run-lock.json`、`source-bindings.json`、全部24组launch/result/ledger/grade和38条父子轨迹，以Conda aster执行轻量JSON/哈希汇总，没有执行候选代码、测试、诊断或模型请求。统计直接来自原始结果，不依赖待重新生成的summary/pairs/results；未修改冻结源码、数据或PROTOCOL。

## 输入与执行一致性

- **24/24结果、12/12配对完整**，无额外/重复/未完成episode目录；每道题3次、每次两臂。24个父运行和14个子运行均有checkpoint、trace与请求目录。实际launch顺序符合冻结排程，6对Solo先、6对Worker先，episode间没有时间重叠；没有从三个重复中择优计分。
- 两臂均绑定 `/tmp/aster-worker-focused-source-20260929` 的 `856345eab891b327d61466e6c27b79d908be6b98`。**54个Aster源码模块**的当前哈希与24份launch一致；Git HEAD、diff和未追踪源码检查一致。**23个冻结驱动/数据/协议文件**当前哈希与run-lock及保存快照一致。
- 每份launch均记录 `/home/dzh/.conda/envs/aster/bin/python` 与绑定源码的 `aster_import`；父TaskSpec的模型/工具/token上限也与launch一致。两个测试依赖环境的Python二进制与包清单哈希仍匹配锁定值。每次启动前后驱动亦检查冻结身份。
- 同一配对的原始prompt、统一附加说明、公开环境指引、preparation、endpoint哈希、source与limits一致；每道题全部6次的 `product_before` 文件哈希映射一致。该映射分别含Django **6,647**、printing **1,543**、OpenLibrary **2,103**、CDF **1,511** 个产品文件，不包括运行时证据目录。真实父首请求的原始task prompt与launch一致，未注入选题理由、遗漏类别或隐藏解法。
- 配置仅 `max_workers` 为Solo **0**、Worker **2**；共同使用Responses、`gpt-5.6-luna`、64,000上下文、8,192输出上限，关闭记忆与环境复用。endpoint SHA-256相同；688次成功响应自报模型亦全部一致。子调用包含在团队限额与计费中，没有额外团队预算或廉价子模型。

| 难度 | 全队模型调用上限 | 全队工具上限 | 内部/外部报告token上限 | 秒数上限 |
|---|---:|---:|---:|---:|
| medium | 60 | 90 | 750,000 | 900 |
| hard | 80 | 120 | 1,000,000 | 1,200 |

内部请求准入还预留估计输入和最大输出，因此可在实际报告token尚未到顶时停止；两臂共同遵循该规则。两臂各5个父运行因 `team_token_limit` 停止，全部仍独立评分并计入12次分母；另各7个父运行正常completed/verified，无episode异常、grader_error或缺失要求项。

## 账本与评分证据

| 核对项 | Solo | Worker | 全部 |
|---|---:|---:|---:|
| 逻辑模型请求 | 275 | 413 | 688 |
| 实际HTTP attempts | 277 | 419 | 696 |
| 有total_tokens的attempts | 275 | 413 | 688 |
| 无usage的transport失败 | 2 | 6 | 8 |

696个provider_attempt事件与HTTP账本按payload哈希、attempt号、分类、模型和token逐项一致；attempt累加、逻辑请求累加、父子运行累加与result.usage/ledger_usage四方一致。698份保存请求的payload SHA-256全部复核成功，包含未获预算准入的请求；文件数不是实际调用数。缓存输入已包含在input/total中，reasoning也不应再叠加为额外总token。

四个复用starter/reference gate的哈希、starter失败/reference通过状态均有效；24份复制gate内容与原gate一致。24份 `grading/grade.json` 与result.grade相同；F2P/P2P测试名集合与冻结reference gate一致，要求项数量固定为 **62 / 435 / 8 / 20**，缺失均0。独立评分统一使用 `restore_public_tests_and_runners_v2`、网络关闭、Landlock+seccomp；6条episode中9个测试文件改动已被恢复，不用模型修改后的测试替代原评分。

## 可复核主统计

“正确正常交付”固定为独立passed AND phase=completed AND stop_reason=verified AND无episode error；内部verified不能代替独立passed。

| 12次/臂 | Solo | Worker |
|---|---:|---:|
| 冻结独立通过 | 2/12 | 5/12 |
| 正确正常交付 | 1/12 | 3/12 |
| 已报告总token | 7,355,188 | 8,940,292 |
| 累计运行秒数，不含评分 | 4,315.8 | 5,620.5 |
| 工具记录，含失败/被拒尝试 | 613 | 849 |
| 启动/正常完成子任务 | 0/0 | 14/2 |

按独立通过配对为Worker **3胜0负9平**；按正确正常交付为 **3胜1负8平**。总报告token增加 **21.6%**，总时间增加 **30.2%**。两臂都独立通过的子集仅OpenLibrary-r1与CDF-r2两对：Solo/Worker为1,510,257/1,890,892 token、902.5/1,192.3秒；两臂都正确正常交付的配对子集为空。

| 每题三次：独立通过 / 正确正常交付 | Solo | Worker |
|---|---:|---:|
| Django-16256 | 0/3、0/3 | 0/3、0/3 |
| SymPy-14248 printing | 0/3、0/3 | 0/3、0/3 |
| OpenLibrary authors | 1/3、1/3 | 2/3、1/3 |
| SymPy-13878 CDF | 1/3、0/3 | 3/3、2/3 |

## 报告必须保留的边界

这是选题时已知既往结果的development reuse，只有4个Python题、2中2难，没有简单题；24次运行不等于24道独立题，不能替代原始混合难度评测或声称holdout泛化。此次比较是在同一个修复后源码上开/关Worker，不是旧版与新版的消融比较。

冻结评分为native-equivalent改编，非官方榜单分数。printing解析器会折叠跨文件同名测试；CDF只评分Arcsin加19项P2P，**全部6条CDF独立执行exit_code均为1并有非评分失败记录**，因此其passed不能写成整个测试套件或十二种CDF均正确。non_scoring_failures含解析别名，不能直接把条目数当独立失败测试数。后续公开例诊断应单列，不回写冻结得分。

报告接收、子completed或旧公开测试通过不自动证明子贡献；具体贡献需以各任务审计的修改/使用链证据为准。时间还包含服务延迟与重试，不能单独证明并行效率；8次无usage失败的真实用量/费用未知，当前只有报告token，没有可核实美元成本。
