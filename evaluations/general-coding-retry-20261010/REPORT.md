# Aster 五题故障与环境诊断重跑

**五题各重跑一次，独立通过 3/5；其中内部正常完成且独立通过 2/5。按用户后续要求，纳入二十题后的合并成绩为 15/20（75%），见[三方对比表](combined/REPORT.md)。原始单次记录保留为 12/20（60%）。**

使用原 Aster 源码 `c6b651d29f2e9c3de8074d3d90c4ee0c103adca0`、原题面、原镜像、同一模型 `gpt-5.6-luna` / medium，以及每题 900 秒预算。每题从初始代码和空会话开始。仅运行 Aster，最多并发两题；pytest 和 gRPC 使用下述诊断环境适配。原候选、失败测试与参考答案未提供给解题 Agent。

| 任务 | 独立评分 | Aster 内部状态 | 执行秒数 | 主要结果 |
|---|---|---|---:|---|
| [custom-memory-heap-crash](results/aster/terminal-custom-memory-heap-crash/result.json) | **通过，6/6** | 停止，验收要求未满足 | 169.5 | release 崩溃相关原测试通过；内部检查仍受沙箱路径和 Valgrind 限制影响 |
| [cobol-modernization](results/aster/terminal-cobol-modernization/result.json) | **通过，3/3** | `completed / verified` | 542.1 | 数据更新及其余原测试通过 |
| [django-11400](results/aster/django__django-11400/result.json) | **未通过，空补丁** | 停止，`edit_missing` | 787.2 | Python 3.6 无法启动评测沙箱，修改工具持续报错 |
| [pytest-10356](results/aster/pytest-dev__pytest-10356/result.json) | **未通过，目标 0/1、规定回归 79/79** | 900 秒超时，无最终答复 | 900.1 | 补丁未满足 `test_mark_mro`；导入问题缓解后仍有实现遗漏与测试环境问题 |
| [kv-store-grpc](results/aster/terminal-kv-store-grpc/result.json) | **通过，7/7** | `completed / verified` | 326.0 | 服务持续运行，端口与 RPC 功能均通过原测试 |

三个终端任务使用原测试断言且执行数与参考预检一致。pytest 使用官方 SWE-bench 评分器，补丁成功应用，所有 79 项规定的 P2P 通过，但目标 F2P 失败。Django 空补丁按冻结规则直接记未通过。独立评分没有基础设施错误。

## 环境适配与剩余问题

**gRPC 的服务生命周期问题得到直接验证。** 本轮仅为该题提供在专用 Docker 容器 `/app` 中执行的 shell，成功命令的后台服务持续到评分结束；正式 `run_tests` 仍使用临时副本与 Landlock/seccomp。原配置会在 shell 结束时杀掉后台进程。本轮四个交付文件（proto、两个生成接口、server.py）与原轮逐字节相同，原测试从 5/7 变为 7/7。这支持将原失败具体归于服务生命周期；适配仅用于本次诊断，生产 Aster 默认行为尚未修复。

**pytest 的导入障碍已处理，但题目仍未解决。** 本轮使 editable 导入跟随执行副本的 `src`，并把镜像已有、但被 Git 忽略的 `_pytest/_version.py` 纳入容器临时基线，以便复制进沙箱。该文件没有进入候选补丁。环境说明提醒使用相对路径。预检通过 5 条原公开测试，正式运行也实际执行了 `testing/test_mark.py`。但内部运行仍出现 pytester 子测试的未知 marker 配置错误，Aster 在处理失败时耗尽预算。独立评分的失败点仍是 `get_unpacked_marks(C)` 返回 generator，而目标断言要求具体标记列表；不能把这一失败全部解释为环境问题。

**Django 暴露了此前未定位的解释器兼容问题。** 此题使用 Python 3.6，原评测适配器用该解释器运行 Aster 的 `linux.py`；函数注解 `tuple[Path, ...]` 在启动时触发 `TypeError: 'type' object is not subscriptable`。轨迹中 `run_tests`、`patch_file` 和 `write_file` 均受影响。Aster 最终没有产生修改，内部因 `edit_missing` 停止。这次不是供应商中止，也没有达到整题时间上限；保持本次失败，不增加尝试。

**C++ 独立通过，但内部验收未完成。** 其自建检查访问 `/app/user.cpp` 遭到沙箱拒绝；Valgrind 因不能读取 `/proc/self/maps` 而失败。Aster 随后通过了一个不同的编译器可用性测试，验收器仍要求解决此前失败，最终 `completion_requirements_failed`。不能将它算作内部正常完成，也不能因内部失败否认独立 6/6 的结果。

COBOL 的最终答复也披露了直接执行 `/app/program.py` 的沙箱限制，内部主要验收了自建的数据格式测试；独立原测试确认最终候选通过。两道原先由供应商故障中止的题，本次都通过独立评分，但仅 COBOL 同时完成内部验收。

## 用量与证据

| 任务 | 已报告总 token | 请求数 | 异常 / 缺失用量请求 |
|---|---:|---:|---:|
| C++ 堆崩溃 | 241,306 | 17 | 0 / 0 |
| COBOL | 297,418 | 17 | 0 / 0 |
| Django 11400 | 425,578 | 21 | 0 / 0 |
| pytest 10356 | 618,221 | 26 | 1 / 1 |
| gRPC | 133,440 | 11 | 0 / 0 |
| **合计** | **1,715,963** | **92** | **1 / 1** |

已报告输入 1,681,843、输出 34,120、缓存输入 902,656 token；累计 Agent 执行时间 45.4 分钟，并发运行的实际等待时间更短。唯一异常为 pytest 达到时间上限后连接中断造成的 `BrokenPipeError`（HTTP 200，未报告用量）；没有供应商错误导致的任务中止。未知用量不计作零成本。环境预检、评分和事后核对均未调用模型。

本轮适配改变了两题的运行条件，且是按既有失败选择的五题诊断。后续合并成绩包含选择性补测和环境适配，不能视为严格同条件单次通过率、完整 benchmark 分数或验收优化的收益估计。执行前的协议和冻结信息保留当时的单列口径；后续统计按用户要求采用全部五题重跑结果替换，规则与来源记录在 `combined/`。

- [协议](PROTOCOL.md)、[冻结信息](manifest.json)、[环境预检](environment-preflight.json)。
- [逐题结果](results.json)、[汇总](summary.json)、[最终核对](audit.json)。
- `results/aster/<task>/` 保存最终状态、用量、候选补丁（仓库题）、压缩原测试日志和验收事件。
- 完整原始材料位于 `artifacts/private/general-coding-retry-20261010/`，独立仓库评分标识为 `general-retry-aster-20261010`；`collect.py` 仅从原始材料整理结果，不调用模型。

五次正式运行及独立判分均已结束；专用模型网关、任务容器和隔离网络已清理。原始报告与成绩、生产源码保持不变。新增评测脚本通过 Ruff 和 Python 编译检查；未提交或推送。
