# 运行与评分环境

Aster 主进程使用现有 Conda `aster` 的 Python 3.12；任务源码及评分使用独立 Python 3.9 环境。没有修改 Aster 的运行依赖或生产代码。

一般环境用 Conda 创建 Python 3.9，再安装 `requirements.txt`；完整 pip 版本见 `environment-pip-freeze.txt`。Xarray 使用独立 conda-forge 环境，先安装 Python 3.9、NumPy 1.23.5、cdms2 3.1.5、pip 24.0，再安装 `requirements-xarray.txt`；最终版本见 `xarray-conda-explicit.txt` 与 `xarray-pip-freeze.txt`。

这些环境复现原测试语义，但并非上游 Docker 镜像。本次需要的适配如下：

- 沿用 `evaluations/worker-code-20260928/repository_adapter.py`，隐藏测试仅在独立评分副本中加入；先恢复公开测试和运行器，避免候选修改评分条件。
- Sphinx 去除 tox 编排，直接用同一个测试选择运行 pytest。Django 与 SymPy 使用仓库自带测试入口。
- 日志解析使用已固定的 SWE-bench parser，SHA-256 为 `cd56156414f8327221e525665ace9b184f7d73e83b272d9eb3f545fb17c2d9bc`。Xarray 按上游别名调用 `parse_log_pytest`；Django 对本地 unittest 连在同一行的测试 ID 和 subtest 失败记录做格式归一化，保留失败状态，不改通过判据。
- Xarray 的旧测试依赖 `pytest.warns(None)` 及旧版异常/警告行为，因此选用 pytest 6.2.5。补齐 sparse、numexpr、numbagg、flox、Iris、cdms2 等被原始 P2P 清单要求的可选库；Iris 3.4.1 使用 xxhash 3.2.0，避免新版仅接受 bytes 的接口变化。
- 上述准备中失败的检查仍保存在私有 `preflight/*-environment-v*` 中。模型未参与环境校准；未删除必需测试、未把跳过当作通过。选定测试文件中不在原始 F2P/P2P 名单内的其他失败单列于 `non_scoring_failures`。

源材料保存在 `artifacts/private/swebench-verified-20261008/`。`prepare.py` 按冻结清单获取源码归档；`evaluate.py preflight <instance_id>` 做原版/参考补丁校验，`evaluate.py run <instance_id>` 执行一次 Aster 并独立评分，`summarize.py` 只复算已有结果。各模型进程独立、至多两个同时运行；后台记忆和所有已知请求消耗都计入结果。

不要把整个私有目录挂入 Agent 工作区。公开清单不含参考补丁或隐藏测试正文，控制端缓存和评分工作区由 Landlock 路径边界隔离。模型可见的源码来自题目 `base_commit`，题面仅使用 `problem_statement`，不使用 `hints_text`。
