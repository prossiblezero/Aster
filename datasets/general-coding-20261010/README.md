# Aster 通用编码小集（2026-10-10）

20 道原始任务，覆盖 12 道仓库修复、6 道终端编码和 2 道小型实现。用于当前 Aster、pi 和 Codex 的同模型比较。它是固定配比的小规模评测，不是任何原 benchmark 的完整榜单成绩。

已完成每种 Agent 每题一次、共 60 次运行及独立评分：**Aster 12/20（60%）、pi 15/20（75%）、Codex 14/20（70%）**。仓库部分均为 8/12；耗时、用量、验收误报、供应商故障、shell/沙箱环境差异和原测试覆盖边界见[完整报告](../../evaluations/general-coding-20261010/REPORT.md)。本轮没有修改产品、换题或补跑取最好结果。

选题不使用协作机会或可拆分性标签。17 题未出现在此前 Aster 的相关任务清单中；3 题曾用于开发，分组报告。不保证这些公开任务未被基础模型预训练见过。

| 任务 | 来源 | 上游难度 | Aster 历史题 |
|---|---|---|---|
| `django__django-17029` | SWE-bench Verified | <15 min fix | 否 |
| `django__django-11400` | SWE-bench Verified | 1-4 hours | 否 |
| `sympy__sympy-23262` | SWE-bench Verified | 15 min - 1 hour | 否 |
| `sympy__sympy-17630` | SWE-bench Verified | 1-4 hours | 否 |
| `sphinx-doc__sphinx-9230` | SWE-bench Verified | <15 min fix | 否 |
| `sphinx-doc__sphinx-11510` | SWE-bench Verified | 1-4 hours | 否 |
| `pydata__xarray-7233` | SWE-bench Verified | 15 min - 1 hour | 否 |
| `pydata__xarray-3677` | SWE-bench Verified | 15 min - 1 hour | 否 |
| `pytest-dev__pytest-7571` | SWE-bench Verified | 15 min - 1 hour | 否 |
| `pytest-dev__pytest-10356` | SWE-bench Verified | 1-4 hours | 否 |
| `psf__requests-5414` | SWE-bench Verified | <15 min fix | 否 |
| `psf__requests-2931` | SWE-bench Verified | 15 min - 1 hour | 否 |
| `terminal-polyglot-c-py` | Terminal-Bench archive | medium | 否 |
| `terminal-cancel-async-tasks` | Terminal-Bench archive | hard | 是 |
| `terminal-custom-memory-heap-crash` | Terminal-Bench archive | medium | 否 |
| `terminal-schemelike-metacircular-eval` | Terminal-Bench archive | medium | 否 |
| `terminal-cobol-modernization` | Terminal-Bench archive | easy | 是 |
| `terminal-kv-store-grpc` | Terminal-Bench archive | medium | 是 |
| `aider-simple-linked-list` | Aider Polyglot Python | unlabelled | 否 |
| `aider-sgf-parsing` | Aider Polyglot Python | unlabelled | 否 |

仓库题固定六个 Python 项目，每项目两题。按专家修复时间分层后以种子 20261010 的 SHA-256 排序抽取；没有可用较长任务的项目使用预定回退规则。仓库部分实际为 8 道小于一小时、4 道 1–4 小时，没有大于四小时的任务。终端部分按六类工作区编码任务人工覆盖，并非全体 Terminal-Bench 的随机样本。Aider 在排除旧题后的 27 道 Python 任务中按固定哈希排序选两题。

[选择记录](selection.json)保留排除的历史 ID、Aider 排序及终端任务范围；[来源](sources.json)固定仓库 revision、下载地址和文件哈希；[任务元数据](tasks.json)记录原始 ID、镜像 digest、题面哈希和语言环境。原题、参考解、隐藏测试与模型原始轨迹存于控制端 `artifacts/private/general-coding-20261010/`，不挂载给模型。

SWE-bench 采用官方 Docker 镜像、官方评分脚本及原 F2P/P2P 条目。终端题使用原生初始镜像、原路径和不变的上游测试断言，测试解释器统一适配到 Python 3.12；测试依赖及 gRPC 依赖预装，跳过测试启动器的联网安装步骤。gRPC 参考脚本只移除重复的依赖安装命令。Aider 保留公开接口测试，评分时恢复受保护副本。终端和 Aider 的结果标为本地适配成绩。

所有 20 题已在实际评分环境中确认初始状态失败、参考解通过；终端/Aider 正式评分还要求执行数与参考预检一致且没有跳过。测试数量不作为混合集权重，整题通过才记 1 分。原始断言覆盖有限，不能声称完整防作弊；尤其终端原测试并非每题都有独立进程安全边界。

源码来源各保留原许可证与 canary；Terminal-Bench 仓库 Apache-2.0，Aider Python 练习转引 Exercism MIT（许可证溯源边界沿用 `worker-code-v2/LICENSES`），SWE-bench harness 的 MIT 不替代 Django/SymPy/Sphinx/xarray/pytest/Requests 各项目许可证。任务只用于评测，不进入训练。

[运行协议](../../evaluations/general-coding-20261010/PROTOCOL.md) · [冻结版本](../../evaluations/general-coding-20261010/manifest.json) · [正反预检](../../evaluations/general-coding-20261010/preflight.json)

执行器复用原三方 Docker 运行器的容器文件传输、模型 gateway 和 SWE-bench 官方评分器。新代码仅位于本轮评测目录，未修改 Aster 生产代码。
