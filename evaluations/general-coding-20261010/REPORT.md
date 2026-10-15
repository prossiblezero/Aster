# Aster / pi / Codex：20 题通用编码小集

**60/60 次正式运行和独立评分已完成：Aster 12/20（60%），pi 15/20（75%），Codex 14/20（70%）。**

三者在同一模型 `gpt-5.6-luna`、每题 900 秒预算下各运行一次。12 道仓库修复题的通过任务完全一致，均为 8/12；总分差异来自终端和小型实现题。本轮没有修改 Aster、换题或补跑取最好结果。

| Agent | 总体 | 仓库修复 | 终端编码 | 小型实现 | 本项目新增题 | 历史题 |
|---|---:|---:|---:|---:|---:|---:|
| Aster | **12/20（60%）** | 8/12 | 2/6 | 2/2 | 12/17 | 0/3 |
| pi | **15/20（75%）** | 8/12 | 6/6 | 1/2 | 12/17 | 3/3 |
| Codex | **14/20（70%）** | 8/12 | 4/6 | 2/2 | 13/17 | 1/3 |

所有 35 份非空仓库补丁均完成官方评分，规定的 P2P 均通过；未解决的仓库题失败在目标 F2P。Aster 的一份空补丁直接记未通过。终端/Aider 的 24 次评分均执行预期数量的测试；评分阶段没有基础设施错误。

**这轮未证明 Aster 整体优于对照，也不能证明验收优化提高了通过率。** 运行期间出现供应商故障、超时，以及 shell/沙箱路径与依赖使用差异；尤其 Codex 的 gRPC 运行被登录 shell 重置 PATH 所影响。本表是本轮冻结环境下的端到端观测，不应当作稳定能力排名。

## 逐题结果

每格链接包含该次耗时、用量、最终答复和独立测试状态。† 为达到 900 秒上限；‡ 为供应商错误中止。补丁通过与正常交付分开统计。`历史` 指此前用于 Aster 开发，不表示另外两种 Agent 或基础模型是否见过。

| 任务 | Aster | pi | Codex |
|---|---|---|---|
| django__django-17029 | [通过](results/aster/django__django-17029/result.json) | [通过](results/pi/django__django-17029/result.json) | [通过](results/codex/django__django-17029/result.json) |
| django__django-11400 | [未通过†](results/aster/django__django-11400/result.json) | [未通过](results/pi/django__django-11400/result.json) | [未通过](results/codex/django__django-11400/result.json) |
| sympy__sympy-23262 | [通过](results/aster/sympy__sympy-23262/result.json) | [通过†](results/pi/sympy__sympy-23262/result.json) | [通过](results/codex/sympy__sympy-23262/result.json) |
| sympy__sympy-17630 | [未通过](results/aster/sympy__sympy-17630/result.json) | [未通过](results/pi/sympy__sympy-17630/result.json) | [未通过](results/codex/sympy__sympy-17630/result.json) |
| sphinx-doc__sphinx-9230 | [通过](results/aster/sphinx-doc__sphinx-9230/result.json) | [通过](results/pi/sphinx-doc__sphinx-9230/result.json) | [通过](results/codex/sphinx-doc__sphinx-9230/result.json) |
| sphinx-doc__sphinx-11510 | [未通过](results/aster/sphinx-doc__sphinx-11510/result.json) | [未通过](results/pi/sphinx-doc__sphinx-11510/result.json) | [未通过](results/codex/sphinx-doc__sphinx-11510/result.json) |
| pydata__xarray-7233 | [通过](results/aster/pydata__xarray-7233/result.json) | [通过](results/pi/pydata__xarray-7233/result.json) | [通过](results/codex/pydata__xarray-7233/result.json) |
| pydata__xarray-3677 | [通过](results/aster/pydata__xarray-3677/result.json) | [通过](results/pi/pydata__xarray-3677/result.json) | [通过](results/codex/pydata__xarray-3677/result.json) |
| pytest-dev__pytest-7571 | [通过](results/aster/pytest-dev__pytest-7571/result.json) | [通过](results/pi/pytest-dev__pytest-7571/result.json) | [通过](results/codex/pytest-dev__pytest-7571/result.json) |
| pytest-dev__pytest-10356 | [未通过](results/aster/pytest-dev__pytest-10356/result.json) | [未通过](results/pi/pytest-dev__pytest-10356/result.json) | [未通过](results/codex/pytest-dev__pytest-10356/result.json) |
| psf__requests-5414 | [通过](results/aster/psf__requests-5414/result.json) | [通过‡](results/pi/psf__requests-5414/result.json) | [通过](results/codex/psf__requests-5414/result.json) |
| psf__requests-2931 | [通过](results/aster/psf__requests-2931/result.json) | [通过](results/pi/psf__requests-2931/result.json) | [通过](results/codex/psf__requests-2931/result.json) |
| terminal-polyglot-c-py | [通过](results/aster/terminal-polyglot-c-py/result.json) | [通过](results/pi/terminal-polyglot-c-py/result.json) | [通过](results/codex/terminal-polyglot-c-py/result.json) |
| terminal-cancel-async-tasks（历史） | [未通过](results/aster/terminal-cancel-async-tasks/result.json) | [通过](results/pi/terminal-cancel-async-tasks/result.json) | [未通过](results/codex/terminal-cancel-async-tasks/result.json) |
| terminal-custom-memory-heap-crash | [未通过‡](results/aster/terminal-custom-memory-heap-crash/result.json) | [通过](results/pi/terminal-custom-memory-heap-crash/result.json) | [通过](results/codex/terminal-custom-memory-heap-crash/result.json) |
| terminal-schemelike-metacircular-eval | [通过](results/aster/terminal-schemelike-metacircular-eval/result.json) | [通过](results/pi/terminal-schemelike-metacircular-eval/result.json) | [通过](results/codex/terminal-schemelike-metacircular-eval/result.json) |
| terminal-cobol-modernization（历史） | [未通过‡](results/aster/terminal-cobol-modernization/result.json) | [通过](results/pi/terminal-cobol-modernization/result.json) | [通过](results/codex/terminal-cobol-modernization/result.json) |
| terminal-kv-store-grpc（历史） | [未通过](results/aster/terminal-kv-store-grpc/result.json) | [通过](results/pi/terminal-kv-store-grpc/result.json) | [未通过](results/codex/terminal-kv-store-grpc/result.json) |
| aider-simple-linked-list | [通过](results/aster/aider-simple-linked-list/result.json) | [未通过‡](results/pi/aider-simple-linked-list/result.json) | [通过](results/codex/aider-simple-linked-list/result.json) |
| aider-sgf-parsing | [通过](results/aster/aider-sgf-parsing/result.json) | [通过](results/pi/aider-sgf-parsing/result.json) | [通过](results/codex/aider-sgf-parsing/result.json) |

若进一步要求“进程正常结束、存在最终答复且独立通过”，Aster 为 **12/20**，pi **13/20**，Codex **14/20**。pi 的 SymPy 23262 虽超时，已有补丁通过；Requests 5414 因流错误中止，但已有补丁也通过。两次均保留在主指标中，同时标明未正常交付。

## 用量与耗时

| 指标 | Aster | pi | Codex |
|---|---:|---:|---:|
| 已报告输入 token | 8,234,040 | 6,440,927 | 6,434,106 |
| 其中缓存输入 | 3,955,712 | 5,761,536 | 5,627,136 |
| 已报告输出 token | 116,285 | 86,522 | 94,160 |
| 已报告总 token | 8,350,325 | 6,527,449 | 6,528,266 |
| 请求数 | 413 | 293 | 268 |
| 失败/中断请求数 | 18 | 7 | 9 |
| 缺失用量请求数 | 18 | 7 | 9 |
| 累计 Agent 执行时间（分钟） | 141.6 | 91.3 | 89.6 |
| 每题中位耗时（秒） | 453.3 | 237.7 | 233.4 |
| 超时次数 | 1 | 1 | 0 |

Aster 的已报告总 token 比 pi 多 27.9%，累计执行时间多 55.1%；比 Codex 分别多 27.9%、58.0%。总 token 包含重复输入和缓存输入，不等于唯一上下文长度或账单金额。三组均有缺失用量，因此这些是**已报告量**，不是完整费用比较。

正式请求共 974 次，已报告总量 **21,406,040 token**。Aster 的 3 次后台记忆请求返回普通 JSON，汇总时从原始响应补回 **20,836 token**；原 gateway 的流式元数据未覆盖这三次，原记录未改写。pi 的一条 SSE `error` 也由原响应补充识别。

| 非 benchmark 接入检查 | 已报告 token | 缺失用量请求 | 执行秒数 |
|---|---:|---:|---:|
| Aster | 67,396 | 2 | 198.270 |
| pi | 17,322 | 0 | 18.763 |
| Codex | 22,826 | 1 | 198.811 |

三次接入检查均通过，与正式分数分开；另计 107,544 已报告 token，全部模型调用合计 **21,513,584 已报告 token**。pi/Codex 的接入检查有 `agent-state` 归档 NotFound 记录，原输出、补丁和用量仍保留；60 次正式尝试没有归档错误。环境预检、官方评分和事后诊断均未调用模型。

## 验收准确性

| 最终答复分类 | Aster | pi | Codex |
|---|---:|---:|---:|
| 明确声明成功 | 11 | 14 | 15 |
| 其中独立评分失败 | **4/11（36.4%）** | **4/14（28.6%）** | **3/15（20.0%）** |
| 带保留条件 | 2（失败 0） | 3（失败 0） | 5（失败 3） |
| 无最终答复 | 3（失败 3） | 3（失败 1） | 0 |
| 仅报告验证结果，任务完成声明有歧义 | 4（失败 1） | 0 | 0 |

Aster 内部 `completed/verified` 为 **17/20**，其中 **5/17** 未通过独立评分：SymPy 17630、Sphinx 11510、pytest 10356、异步取消和 gRPC。不能把内部完成率 85% 写成 benchmark 通过率。把四份歧义答复也并入成功声明时，Aster 为 **5/15** 声明后独立失败，避免标签选择掩盖结果。

7 次 Aster 运行共有 9 次完成返修提示：2 次要求处理已见测试失败，7 次要求通过正式 `run_tests`。这证实拦截机制实际触发；仍然存在范围不完整的测试、自建静态测试以及服务未持续运行的问题。没有改动前的同条件运行，不能给出验收优化的因果提升幅度。

标签依据最终答复人工判定，定义与逐条理由见[标注文件](completion_claims.json)。36 次仓库标注全部在正式官方评分前完成；24 次终端/Aider 中 23 次在读取评分前标注，Codex 异步取消题因完成时间与批量读取重叠，标注时已看过失败结果，已单独注明。不同答复风格影响分母，这只是本轮描述统计。


## 范围与版本

本轮固定 20 道原始任务，每题每种 Agent 一次，共 60 次正式尝试。
使用当前 Aster `c6b651d29f2e9c3de8074d3d90c4ee0c103adca0`、pi 1.1.0、
Codex CLI 0.154.0，三者均使用 `gpt-5.6-luna` 的 Responses 接口、medium reasoning。
本轮不修改生产代码，不根据成绩换题或挑选重跑结果。

| 任务组 | 数量 | 选取与评分 |
|---|---:|---|
| SWE-bench Verified 仓库修复 | 12 | 六个 Python 仓库各两题；按修复时间分层、固定哈希选取；官方 Docker 镜像与 F2P/P2P 评分 |
| Terminal-Bench 归档任务 | 6 | 人工覆盖六类工作区编码任务；原测试断言在本地适配环境执行 |
| Aider Polyglot Python | 2 | 排除旧题后固定哈希选择；恢复原公开测试后评分 |

17 题未在此前 Aster 的相关任务清单中出现；3 道终端题曾参与开发，单列成绩。
这是固定配比的通用编码小集，Python 仓库占主导，终端题也不是对整个来源的随机抽样。
不把旧 26 题 Worker 专项强行并入，不使用可拆分性选题，也不声称这些公开题未被模型训练见过。
结果不是任一来源的完整榜单得分。

完整题单、来源版本、历史题排除规则、镜像和题面哈希见
[数据集](../../datasets/general-coding-20261010/README.md)、[选择记录](../../datasets/general-coding-20261010/selection.json)、
[来源](../../datasets/general-coding-20261010/sources.json)和[冻结清单](manifest.json)。

## 运行和评分条件

每次从独立容器、空会话和空记忆开始，不加载操作者配置、skills 或历史答案。
三者保留各自的系统提示词、工具和上下文压缩策略；均使用单 Agent。
Aster 保留默认记忆和后台整理，启用严格完成校验和沙箱；pi 禁用用户扩展；
Codex 关闭多 Agent 和联网搜索。容器仅能访问控制端模型代理和隔离网络中的本地服务，
供应商凭据保留在控制端。

每次 Agent 执行上限 900 秒，4 CPU、8 GiB 内存；最多三次并发。
上下文窗口 64,000，控制端统一约束单次输出上限 8,192 和 reasoning effort。
耗时统计 Agent 进程执行时间，包含正常收尾；环境准备和独立评分不计入该时间。
Aster 另有 500 轮/500 工具步上限，不以工具次数对齐不同框架，也没有未统一执行的 token 总额限制。

20/20 题均已在实际评分环境完成“初始状态失败、参考解通过”预检。
仓库题核对了规定的 F2P/P2P 测试确实出现；空补丁直接记未通过。
终端题使用原生任务镜像和路径，预装依赖，测试解释器适配为 Python 3.12；
gRPC 参考脚本只移除重复的联网安装命令。隐藏测试在 Agent 结束后注入，Aider 恢复原测试副本。
终端/Aider 必须达到参考预检的测试数量，且没有失败、错误或跳过才算整题通过。
原测试的覆盖面有限，评分也不具备完全防恶意代码篡改的隔离保证。

主指标为独立整题通过数/20，每题等权；不按测试断言数量加权。
超时后留下的补丁仍接受评分，超时和正常交付数另列。
进程退出码、Agent 的完成声明、Aster 内部 `completed/verified` 和独立测试通过分别统计。
[完成声明标注](completion_claims.json)按同一规则区分成功、带保留条件、未完成、无最终答复及歧义；
仓库题的标注在查看正式官方评分前完成。
“声明成功但独立失败/声明成功次数”用于观察验收准确性，不能替代总体通过率。

没有改动前版本的同条件对照，因此不能将本轮与旧题单成绩之差归因于验收优化。
每题只有一次样本，结果只描述本次固定题单与预算，不能据此声称稳定排名。
完整规则见[冻结协议](PROTOCOL.md)，正反预检见[预检记录](preflight.json)。

## 证据与复算

`evaluate.py` 负责隔离运行、用量代理、评分入口和补丁导出，复用原三方对照的 Docker 与官方评分工具。
`prepare.py` 从固定来源缓存生成题目，`aster_episode.py` 加载冻结的 Aster 源码。
正式执行前后均核对源码、运行器、CLI 二进制和题单哈希。
完整原始请求、响应、候选代码及容器归档保存在
`artifacts/private/general-coding-20261010/`；原始材料不挂载给 Agent。

现有运行环境中的查询与复算命令：

```bash
python evaluations/general-coding-20261010/summarize.py status
python evaluations/general-coding-20261010/summarize.py results
```

正式尝试存在时执行器跳过已完成结果；遇到有启动记录但无结束记录的尝试会拒绝覆盖。
不应删除记录后重新调用模型。独立仓库评分使用该轮私有目录的
`repository-dataset.json` 和各 Agent 的 `*.predictions.jsonl`，运行标识为
`general-aster-20261010`、`general-pi-20261010`、`general-codex-20261010`。

用量以控制端收到的供应商记录为准，包含失败请求、原生重试和后台记忆整理。
未报告用量的请求单列，不能当作零成本；没有可信单价，不换算金额。
正式运行和三次非 benchmark 接入检查分别统计，确定性的环境预检没有调用模型。

## 具体失败与环境边界

`pytest-dev__pytest-7571` 的 Aster 运行中，验收器拒绝了 `Ran 0 tests`，
但最终接受了一条仅检查源码字符串存在的自建 unittest，内部状态为 `completed/verified`。
最终答复明确承认没有执行原生 pytest 测试，因此完成声明标注为 `qualified`。
轨迹里还出现了 `cd /testbed` 后的沙箱访问拒绝、`No module named pytest`，
以及原目录没有 Git 元数据的提示；不能把模型的“pytest 未安装”解释直接当作环境事实。
同题 pi 报告原 logging 测试 57 项通过，评分端参考解预检也已通过。
这反映了沙箱路径、仓库解释器和验证选择之间的实际使用边界；本轮保留现状及原始轨迹，
最终官方评分中该题通过；另一个使用自建测试验收的 pytest 10356 未通过 `test_mark_mro`。因此这里同时保留验证覆盖不足与独立判分结果。


- **跨路径与边界遗漏。** 三者都未通过 Django 11400、SymPy 17630、Sphinx 11510、pytest 10356。Aster 的 Django 为空补丁；pi/Codex 的 Django 漏掉底层字段 choices 排序。SymPy 的 block multiplication 修复仍未使 `Add(ZeroMatrix, ZeroMatrix)` 保持矩阵类型。Sphinx 的 include 内容事件未正确处理截取边界。已有 P2P 全通过不等于原缺陷已修复。
- **异步清理遗漏。** Aster、Codex 在 3 个任务、并发上限 2 的 SIGINT 场景都只打印两次启动、零次清理；原测试要求两次清理。pi 通过六项检查。
- **服务交付遗漏。** Aster gRPC 的临时测试通过，但评分时没有持续运行的服务，7 项中 2 项失败。Codex 的 7 项中 3 项失败；它的 `bash -lc` 将 PATH 重置到系统 Python，绕过 `/opt/task-python/bin/python` 中预装的 grpcio 1.73.0。事后在相同镜像验证：`bash -c` 能导入，`bash -lc` 不能导入。这是本轮运行配置与原生工具行为的已知交互，不把全部差距归为编码能力。
- **验收引起的范围扩大。** Aster Requests 5414 为清除网络环境触发的超时测试失败，修改了 `HTTPAdapter` 对 `NewConnectionError` 的分类；Requests 2931 则放宽了两个测试，接受 `Network is unreachable`。两个补丁都通过官方规定的 F2P/P2P，但这些额外改动不能当作已证明合理的产品修复。仅检查“之前失败的名字现在通过”还不够。
- **供应商中止。** Aster C++ 题在三次传输超时后中止，保留的候选 release 仍段错误；COBOL 题在三次临时 HTTP/连接失败后中止，候选未完成数据更新。pi 链表题因上游 502 中止，20 项均失败。另有 pi Requests 5414 的流错误及前述两次任务超时。原生请求重试和全部用量均保留，没有重新启动这些题取更好结果。
- **原测试的能力边界。** C++ 原测试虽声称要防止 `quick_exit()` 绕过析构，pi 的 `quick_exit(0)` 实现仍通过六项检查；原断言不足以保证修复语义完整。本轮按冻结规则保留得分，后续版本需先修正测试再冻结。解释器题实际比较 63 个程序（含预设嵌套案例），三者均通过；pi 自述失败的 `(+ 7 8)` 嵌套例在相同镜像的事后复现中三者都正确输出 15，不能据自述断言实现缺陷。

[事后环境诊断](diagnostics.json)保留相同镜像的解释器探测和三份原候选的嵌套执行结果，没有模型调用，没有改动正式分数。[最终核对](audit.json)确认 20 题、60 次唯一尝试、974 个模型请求、60 个独立判分，以及每题三方题面/镜像一致、源码和参数冻结一致。

## 结果文件

- [汇总数据](summary.json)、[逐次结果](results.json)、[完成声明](completion_claims.json)。
- `results/{aster,pi,codex}/{task}/`：每次运行的 `launch.json`、`result.json`、`usage.json`、压缩原测试输出；仓库题另有候选补丁，Aster 另有验收事件摘录。完整原始轨迹仍保留在控制端私有目录。
- [预检](preflight.json)、[协议](PROTOCOL.md)、[冻结版本](manifest.json)、[一致性核对](audit.json)。

三份官方评分进程均正常结束；评测脚本的 Ruff 和 Python 编译检查通过。生产源码未修改，未提交或推送。
