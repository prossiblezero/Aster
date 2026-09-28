# 委派机制触发审计

本审计只读已经生成 `result.json` 的样本；不新增模型请求，不修改冻结源码、协议或控制器。它检查“触发 → 父请求实际收到 → 后续行为 → 独立结果”，不把 Worker 启动、完成或交付次数当作收益。前三道 medium 的贡献分析由相邻审计负责，此处从 `swe-verified-django__django-13121` 开始。

这是截至 34/78 个 episode 完成时的代表性案例审计（2026-09-29），不是全部实验的汇总。下列 token 是供应商报告的全团队累计使用量，含父和所有子运行；不表示美元账单。时间为 episode 运行时间，不含独立评分。评分通过与运行正常交付是两个指标。

## 案例 1：partial 能保住观察，但更多子任务预算不等于更好的调查

任务：`swe-verified-django__django-13121`，修复 SQLite/MySQL 的纯 DurationField 算术表达式。

| Arm | 独立评分 | Reported tokens | Model / tools | 秒 | HTTP usage 覆盖 | 实际停止原因 |
|---|---:|---:|---:|---:|---:|---|
| solo | 通过 | 750,621 | 28 / 53 | 404.9 | 28 / 29 | 外部团队预算 |
| handoff_only | 通过 | 770,689 | 35 / 69 | 662.3 | 35 / 35 | 外部团队预算 |
| redesigned | 通过 | 697,090 | 27 / 68 | 509.0 | 27 / 27 | `team_token_limit` |

solo/handoff_only 的 `result.stop_reason=provider_error` 在 trace 中实际是 `category=evaluation_budget`、`message=evaluation_whole_tree_model_budget`，不能误报成供应商故障。三个 arm 都没有正常给出最终答复。新版比 solo 的报告 token 少 7.1%，工具多 28.3%，时间多 25.7%；solo 有一次 HTTP 无 total usage，不能把报告 token 差值写成精确成本差值，更不能把预算提前停止当作效率提升。

两个委派 arm 都启动一个只读调查 Worker，父同时调查同一问题，最后主动 `worker_status` 取回结果。handoff_only 子运行在 12 工具步停止，使用 64,378 tokens；redesigned 在 24 工具步停止，使用 213,492 tokens（团队的 30.6%）。两者都保存并交付明确的 `partial`：已读哪些文件、未验证什么，以及不能确定最小修复。增加子预算后没有得到完整根因结论。

新版 partial 报告指出 `CombinedExpression` 和后端 duration 路径，但仍猜测输出类型推断，并明确不知道确切触发表达式。父在取回报告之前已读 SQLite UDF（父 trace seq 427）及 `CombinedExpression`（455）；取回后最终修改纯 duration 操作数的分支（785），不能据此认定修复来自子报告。父自行运行聚焦测试和完整 duration 测试类成功（1170、1212）；下一轮因 token 预算停止。预算限制保护了开销上限，也中断了面向用户的最终交付，不能只计前者。

可定位证据（所有私有文件根为 `artifacts/private/worker-redesign-20260928/`）：

| 证据 | 路径 / 定位 |
|---|---|
| 三臂指标与独立评分 | `runs/swe-verified-django__django-13121--{solo,handoff_only,redesigned}/result.json` 与相邻 `grading/grade.json` |
| handoff_only 父 / 子 | `f238e60d831149bca4c86eb9c408f7c3` / `ab1c1c8d8aff438e9c4d910b4d2eb3bd` |
| handoff_only partial | 子 `handoff.json`；父 `trace.jsonl` seq 340 的 `worker_status` 结果 |
| handoff_only 父实际收到 | 父 `requests/0008-52f15951e0f090727f69725e20c0630bca9d707a14b7b11220b67f4ad94a8b38.json` 的输入包含完整 `**PARTIAL handoff**` |
| handoff_only 预算原因 | 父 `trace.jsonl` seq 1795 |
| redesigned 父 / 子 | `ecd3549bfc0d4d5cbcbc50f6fce7b4e5` / `9f45f8ec3a534a27b778d2db96989d59` |
| redesigned partial | 子 `handoff.json`、子 trace seq 793；父 trace seq 481 的 `worker_status` 结果 |
| redesigned 父实际收到 | 父 `requests/0013-6fb8d98d192feef1a2c071a4e057e8bd2456f93e78b3d69b9cbea3ce16327599.json` 的输入包含完整 `**PARTIAL HANDOFF**` |
| redesigned 后续行为 / 停止 | 父 trace seq 785、1170、1212、1216 |
| solo 预算原因 | 父 `3179a0b1f12d4966aaf5254f37d7fbc0` trace seq 1692 |

上述父/子文件均位于 `workspaces/<task>--<arm>/.aster/runs/<run_id>/`。这两个委派 episode 没有 `worker_handoff_delivered`、续问或 scope 冲突；主动取回 partial 已证实，自动交付收益尚未由本案例证明。

## 案例 2：供应商中断后仍能取回状态，但空结论没有调查收益

任务：`swe-verified-django__django-16256`，补全 related manager 的异步创建/更新行为。重点审计 redesigned 的异常交付；三臂已完成，独立评分均为 6 项新修复通过、3 项 generic relations ERROR。

| Arm | 独立评分 | Reported tokens | Model / tools | 秒 | HTTP usage 覆盖 | 实际停止原因 |
|---|---:|---:|---:|---:|---:|---|
| solo | 失败 | 762,216 | 30 / 42 | 532.4 | 30 / 30 | 外部团队预算 |
| handoff_only | 失败 | 756,640 | 35 / 62 | 632.5 | 35 / 35 | 外部团队预算 |
| redesigned | 失败 | 674,552 | 30 / 52 | 731.9 | 29 / 30 | `team_token_limit` |

Worker 在 15 工具步、第四次请求遇到真实的 `incomplete_response`（`responses:failed`，该 HTTP 没有 token usage）。持久化 fallback handoff 明确写出 `delivery=partial`、无改动、缺少验证，以及 `No model conclusion was delivered`，没有把读过一个文件冒充调查完成。父通过 `worker_status` 取得该内容，下一次真实请求也包含它，随后继续自己阅读、修改和测试；没有把 Worker 的失败当作父运行必须立即失败的条件。

这证明失败状态可恢复且交付诚实，但没有证明委派有效。子运行只给出已读 `related_descriptors.py` 的路径，父在取回前也已读该文件；没有可归因的新增结论。全团队报告 674,552 tokens、30 次 model 调用、52 次工具、731.9 秒，HTTP usage 覆盖 29/30；子报告 tokens 为 27,221，不能当作其完整成本。父最终实现的普通 FK/M2M 路径有 6 项 fail-to-pass 测试通过，但 generic relations 的 3 项都是 ERROR（缺少 `content_type_id`），任务独立评分失败。

父在 trace seq 1868 尝试提交最终答复，seq 1869 被正式验证要求退回：项目自定义 `tests/runtests.py` 已通过 `run_shell` 执行，却不计入 `run_tests` 的证据。之后泛用 pytest 失败，父又尝试写额外验证脚本，最后 `team_token_limit` 停止。这是完成判定与项目测试入口不匹配的额外成本，不能归因给 Worker 的调度机制，也不能因 token 停止而宣称节省了无用工作。

| 证据 | 路径 / 定位 |
|---|---|
| 指标 / 独立失败原因 | `runs/swe-verified-django__django-16256--redesigned/result.json`、`grading/grade.json`、`grading/tests.log` |
| 父 / 子运行 | `3e693bb362504ddfb100925d5b571234` / `0ce4a99028c84bb4b5e1f344f83d9435` |
| 真实 provider 中断 | 子 `trace.jsonl` seq 180、182；相邻 `handoff.json` |
| 父取回 partial | 父 `trace.jsonl` seq 273 |
| 父实际请求包含 fallback | 父 `requests/0007-a102948dffeb9f7d0233f98227eb52c082f9ade51fc3e704de824361f73d5b70.json` |
| 父继续修改 | 父 `trace.jsonl` seq 869 |
| 完成判定退回 / 预算停止 | 父 `trace.jsonl` seq 1869、2502 |
| solo 外部预算停止 | 父 `683454fec49943c48d1d478469746f74` trace seq 3354 |
| handoff_only 外部预算停止 | 父 `d9bdac9c98f04082a58bbd17059f985d` trace seq 3037 |

本例同样通过主动 `worker_status` 消费结果，没有自动交付、续问或 scope 冲突。因此，这些未触发的机制不能用本例证明实际任务收益。

## 案例 3：较完整的 partial 进入父上下文，但整合尚未验证就耗尽预算

任务：`swe-verified-sphinx-doc__sphinx-9461`，处理 autodoc 的 classmethod/property 描述符。这里只分析已经完成的 redesigned episode；另两臂尚未完成，不进行胜负比较。

只读 Worker 在 24 工具步停止，交付路径、函数、已有测试与建议，明确注明没有执行运行时探针或测试。子使用 254,525 tokens，占全团队 710,288 的 35.8%。父主动取回后，下一次真实请求包含完整 partial；随后的工具确实读取报告建议的 `sphinx/util/inspect.py:370–410`，可确认报告建议与后续行为相符，但父此前已自行阅读大量相关源码，无法排除独立决策。

父最终改的是 `sphinx/ext/autodoc/importer.py`，并增添 class property fixture；没有采用子提出的直接修改 `isproperty()` 建议。接着在请求第 20 轮时触发 `team_token_limit`，尚未执行成功的测试或提交最终答复。此前 Git 查询和运行时探针均被 shell 策略拦截。独立评分的 3 项 fail-to-pass 全失败。该 episode 共 27 次 model 调用、55 次工具、247.8 秒，HTTP usage 覆盖 27/27。

这个案例说明 partial 保留了可继续调查的材料，但其生成与父重复阅读消耗了实际预算；较短耗时伴随预算中止和任务失败，不能当作性能收益。没有 solo 对照时，也不能断言撤掉 Worker 就会通过。

| 证据 | 路径 / 定位 |
|---|---|
| 指标 / 独立评分 | `runs/swe-verified-sphinx-doc__sphinx-9461--redesigned/result.json` 与相邻 `grading/grade.json` |
| 父 / 子运行 | `a9a51f2aa29048cab4bbb574305bda80` / `b3d2e5bcdaa04354852127379e4b6b4c` |
| 子 partial | 子 `handoff.json`、子 trace seq 767 |
| 父取回 / 实际收到 | 父 trace seq 508；父 `requests/0014-32a27cb6bad1fcf1af0a43e916c90c8ad40aa354d2c58b38ac2e4c9fcd6ca60e.json` |
| 父进一步读建议路径 | 父 trace seq 531 |
| 探针被拦 / 修改 / 停止 | 父 trace seq 650、1140、1308、1316 |

## 这批案例支持什么

四个详细审计的委派 episode（案例 1 两臂，案例 2/3 各 redesigned）均实际收到 partial：三个是模型生成的部分结论，一个是供应商失败后的确定性 fallback。保存状态和已读证据没有因子运行未完成而丢失；这项可靠性行为有真实轨迹证明。

四次交付都由父主动 `worker_status` 消费；没有自动交付事件、`worker_message(resume=True)` 或 scope 冲突。此审计不为未触发机制背书，也不判断它们在剩余任务里不会触发。三个 redesigned 父运行都以 `team_token_limit` 停止；没有一个能证明预算预留足以保障父完成最终整合。

这几例不足以支持“委派提高通过率、降低成本或降低总步数”的简历结论。案例 1 虽报告 token 较少，但更慢、工具更多且预算中止；案例 2/3 本身失败。最终收益必须依据完整配对结果及更明确的成功轨迹判断，不能从机制正常触发直接推导。案例中重复调查、子任务缺少完整触发条件、项目测试入口与正式验证不匹配是后续可诊断的问题；本次只记录证据，不修改实验中的行为。
