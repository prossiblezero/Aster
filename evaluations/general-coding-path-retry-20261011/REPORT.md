# 路径隔离修复后的解释器整题重跑

**完整重跑一次，独立评分仍未通过；这次由三次模型请求超时中止。** 原先受阻的 `/app` 绝对路径已在 Aster 的实际解题过程中执行成功，但模型随后以 `provider_error` 退出，没有写出 `eval.scm`，因此不能新增通过题。

| 项目 | 第二版原记录 | 本次完整重跑 |
|---|---|---|
| 题目 | `terminal-schemelike-metacircular-eval` | 同一题、全新会话与原始镜像 |
| `/app` 自测 | 绝对路径被拒绝 | 原命令成功，输出 `Reading / 15 / Done` |
| 退出状态 | `stopped / stall_recovery_exhausted` | `failed / provider_error` |
| 解题耗时 | 63.504 秒 | 531.604 秒 |
| 独立评分 | 未通过，程序比较 0/63 | 未通过，程序比较 0/63 |

本次前 3 次模型请求成功，Aster 阅读解释器与公开示例后，自主执行 `cd /app && printf '(+ 7 8)\n' | python interp.py test/calculator.scm`，沙箱返回成功。第 4 轮模型调用及两次自动重试分别在约 120.2、120.3、120.3 秒后出现 `ReadTimeout`；网关没有收到上游 HTTP 响应，三次均无用量回执。Aster 第 4 轮、第 9 个工具步骤后停止，工作区没有源文件改动，原始 `interp.py` 哈希保持一致。退出码为 0 只是评测入口正常退出，不表示任务完成。

独立评分在解题进程结束后注入原上游断言并实际执行。顶层 pytest 用例 0/1 通过，其内部 63 个程序比较全部失败；评分器正常运行，没有把供应商故障从分母剔除。路径修复已在真实任务中触发，但本轮没有得到可验收的候选实现，不能据此判断修复后整题能够通过。

| 版本 / Agent | 独立通过 | 通过率 |
|---|---:|---:|
| Aster 第二版完整二十题 | 15/20 | 75% |
| Aster 用本次结果替换解释器一题 | 15/20 | 75% |
| pi 历史固定成绩 | 15/20 | 75% |
| Codex 历史固定成绩 | 14/20 | 70% |

只有解释器题重跑，其余十九题不变；[合并记录](combined/results.json)属于选择性补测，不能称为新版完整二十题单次成绩。此前各轮结果保持原样，pi 与 Codex 未调用模型。

本次共 **7 次请求，41,160 已报告 tokens，3 次缺失用量**。包括 6 次前台请求和 1 次后台维护请求；缺失用量没有按零成本处理。控制器第一次使用缺少 Docker SDK 的 Python，在启动任务或调用模型前退出；随后使用原评测控制器环境启动了本次唯一正式尝试，启动日志保留在私有目录。

原题面、镜像、模型 `gpt-5.6-luna / medium`、64000 上下文、8192 输出、900 秒总时限、4 CPU / 8 GiB 均与第二版相同。当前生产源码相对第二版只改动 `safety/linux.py`、`safety/runner.py`、`tools/testing.py`。为了运行新版沙箱，题目容器新增只读挂载的 Debian 12 bubblewrap 0.8.0，并允许创建挂载命名空间；保留生产 Landlock/seccomp 和发布检查，不替换任务镜像中的系统库。沿用此前的任务解释器和容器进程适配。冻结源码、题面、运行器及上游测试哈希均已核对；任务容器、内部网络和模型网关已清理。

[单题结果](results/terminal-schemelike-metacircular-eval/result.json) · [真实路径执行证据](results/terminal-schemelike-metacircular-eval/path-evidence.json) · [独立测试日志](results/terminal-schemelike-metacircular-eval/tests.txt.gz) · [用量](results/terminal-schemelike-metacircular-eval/usage.json) · [统计](summary.json) · [完整性核对](audit.json)

[事先登记协议](PROTOCOL.md) · [冻结清单](manifest.json) · [相对第二版的生产补丁](source.patch) · [无模型环境预检](environment-preflight.json) · [第二版原报告](../general-coding-optimized-v2-20261011/REPORT.md)
