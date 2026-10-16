# v11 第一轮（Stage 1 + 2）

**第一轮未全部达标。** 已完成并独立评分 32/32 次任务尝试；首次 old20 为 14/20，正常交付且通过为 12/20。稳定通过组 10/10，波动组六题共 12/18。唯一未达标项为纯重读轮数 8（要求 ≤5）。按协议停于第一轮，未运行第二轮，也未自动回滚源码。

下表仅比较首次 old20；pi、Codex 沿用冻结历史结果。加权成本为前台请求的相对估算，不是账单。

| 指标 | Aster v10 | Aster v11 Stage 1+2 | pi（历史） | Codex（历史） |
|---|---:|---:|---:|---:|
| 独立通过 | 13/20 | 14/20 | 14/20 | 15/20 |
| 正常交付且通过 | 12/20 | 12/20 | 13/20 | 15/20 |
| 全部请求 | 410 | 339 | 240 | 254 |
| 总 token（含后台记忆） | 9,820,687 | 7,843,710 | 4,936,079 | 6,212,836 |
| 缺用量请求 | 22 | 3 | 2 | 5 |
| 有用量前台请求 | 378 | 323 | 238 | 249 |
| 加权前台成本 | 3.524968 | 2.966459 | 2.265869 | 2.343249 |

加权前台成本下降 15.84%，但解释器题贡献了净节省额的 84.62%。该题首次在 110.705 秒因沙箱拒绝访问 `/app/interp.py`、停滞恢复耗尽而停止；独立测试通过，但并未正常交付。排除该题后，其余 19 题的前台成本仅从 2.930528 降到 2.844646（2.93%）。这项敏感性分析不改变冻结计分；不能将全部降本归因为优化。

Aster 首次内部判定完成 17 题，其中 5 题未通过独立测试：异步取消、Django-11400、SymPy-17630、pytest-10356、gRPC。独立通过但未正常交付的两题为 requests-5414（completion_requirements_failed）和解释器（stall_recovery_exhausted）。正常交付且通过仍与 v10 相同，为 12/20。

下方保留冻结审计脚本生成的全部门槛、逐题结果和请求异常。详细成本拆分见 [comparison-round1.json](comparison-round1.json)。

源码：`v11-stage2` / `b2b195c24fe90397f672124e4333da5bc8272cf8`。成本仅取 attempt 1 的 old20；重复只用于质量判断。

| 指标 | 实测 | 门槛 | 达标 |
|---|---:|---:|:---:|
| all_32_planned_attempts_scored | True | == True | 是 |
| allowed_attempts_only | True | == True | 是 |
| stable_pass_with_conditional_rescue | True | == True | 是 |
| variable_passes_of_18 | 12 | >= 8 | 是 |
| evidence_errors | 0 | == 0 | 是 |
| fresh_read_blocked | 0 | <= 2 | 是 |
| reread_only_turns | 8 | <= 5 | 否 |
| turns | 304 | <= 325 | 是 |
| cache_ratio | 0.8773815990313286 | >= 0.85 | 是 |
| strict_rewrites_per_followup | 0.009900990099009901 | <= 0.03 | 是 |

| 轮次指标 | v10 | 本轮 attempt 1 |
|---|---:|---:|
| turns | 358 | 304 |
| reread_after_own_edit | 39 | 18 |
| reread_only_turns | 27 | 8 |
| fresh_read_blocked | 11 | 0 |
| search_calls | 102 | 92 |
| search_only_turns | 21 | 25 |
| turns_while_failing | 60 | 54 |
| completion_gates | 10 | 10 |
| preexisting_declared | 0 | 0 |

前台有用量请求：323；搜索轮：25；加权成本：2.96645925（相对估算，非账单）。

| 题目 | 分组 | attempt 1 | attempt 2 | attempt 3 |
|---|---|---|---|---|
| django__django-17029 | 恒过 | 通过 | 未运行 | 未运行 |
| sympy__sympy-23262 | 恒过 | 通过 | 未运行 | 未运行 |
| sphinx-doc__sphinx-9230 | 恒过 | 通过 | 未运行 | 未运行 |
| pydata__xarray-7233 | 恒过 | 通过 | 未运行 | 未运行 |
| pydata__xarray-3677 | 恒过 | 通过 | 未运行 | 未运行 |
| pytest-dev__pytest-7571 | 恒过 | 通过 | 未运行 | 未运行 |
| psf__requests-2931 | 恒过 | 通过 | 未运行 | 未运行 |
| terminal-polyglot-c-py | 恒过 | 通过 | 未运行 | 未运行 |
| aider-simple-linked-list | 恒过 | 通过 | 未运行 | 未运行 |
| aider-sgf-parsing | 恒过 | 通过 | 未运行 | 未运行 |
| django__django-11400 | 恒败 | 失败 | 未运行 | 未运行 |
| sympy__sympy-17630 | 恒败 | 失败 | 未运行 | 未运行 |
| sphinx-doc__sphinx-11510 | 恒败 | 失败 | 未运行 | 未运行 |
| pytest-dev__pytest-10356 | 恒败 | 失败 | 未运行 | 未运行 |
| terminal-cancel-async-tasks | 波动 | 失败 | 通过 | 失败 |
| terminal-custom-memory-heap-crash | 波动 | 通过 | 通过 | 通过 |
| terminal-schemelike-metacircular-eval | 波动 | 通过 | 失败 | 通过 |
| terminal-kv-store-grpc | 波动 | 失败 | 失败 | 失败 |
| terminal-cobol-modernization | 波动 | 通过 | 通过 | 通过 |
| psf__requests-5414 | 波动 | 通过 | 通过 | 通过 |

## 接受的 pre-existing 声明

无。

## 请求异常（全部尝试，包含缺用量）

- {"task": "django__django-17029", "attempt": 1, "background": false, "missing_usage": true, "request_id": "eb4ff8e0573d47eca4e9c3549d7800cd", "http_status": null, "error": "ReadTimeout", "terminal": null, "stream_error": null}
- {"task": "pydata__xarray-7233", "attempt": 1, "background": false, "missing_usage": true, "request_id": "d6d59124165a4ee7bb35cfdc651ab933", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "sphinx-doc__sphinx-11510", "attempt": 1, "background": false, "missing_usage": true, "request_id": "43c04906b0e34d4e8e68856bf7bb3270", "http_status": 200, "error": "BrokenPipeError", "terminal": null, "stream_error": null}
- {"task": "terminal-schemelike-metacircular-eval", "attempt": 3, "background": false, "missing_usage": true, "request_id": "b7acca2f56fa408f8b4ceace461fef51", "http_status": null, "error": "ReadTimeout", "terminal": null, "stream_error": null}

完整证据与累计请求：`audit-round1.json`。
第二轮质量失败时按协议 revert Stage 3；第一轮失败停止，由用户决定是否回滚。

## 本轮全部消耗

首次 20 题用于成本比较；后两批各 6 次只用于质量验证。下表包含后台记忆和失败请求的已返回用量，缺失用量不估填。费用沿用相同权重，只是相对估算。

| attempt | 次数 | 独立通过 | 请求 | 缺用量 | 总 token | 加权费用（已观测） |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 14 | 339 | 3 | 7,843,710 | 3.098510 |
| 2 | 6 | 4 | 117 | 0 | 2,470,502 | 1.004309 |
| 3 | 6 | 4 | 117 | 1 | 2,720,533 | 1.086924 |
| 合计 | 32 | 22 | 573 | 4 | 13,034,745 | 5.189742 |

合计通过数仅为 32 次尝试的原始计数，不构成拼接后的 20 题成绩。全轮 4 次缺用量请求均已列在请求异常中：2 次 ReadTimeout、2 次 BrokenPipeError。请求中断不能一概归为供应商故障；Sphinx-11510 首次达到任务时限后连接中断。原始档案保留在 `artifacts/private/turn-reduction-20261015/round1/`。

## 未达标的纯重读

8 轮分布：Django-11400 2 轮、Sphinx-9230 2 轮、Sphinx-11510 1 轮、SymPy-17630 1 轮、SymPy-23262 1 轮、C/Python 题 1 轮。自身编辑后的强制门禁已降到 0，但模型仍主动重读；本轮不修改冻结门槛或实现。
