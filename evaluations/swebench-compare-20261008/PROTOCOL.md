# Aster / pi / Codex：SWE-bench Verified 十题对比

2026-10-08。沿用前次固定十题，简单/中等/困难为 3/5/2，不按结果更换题目。比较三种 Harness，每题每种一次，共 30 次。前次 Aster 本地评分的 40% 单独保留，不并入本表。

- 固定 Aster 生产源码、pi 1.1.0、Codex CLI 0.154.0，以及官方 SWE-bench 5.0.2（提交 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`）。题目与源码哈希见 `manifest.json`。
- 三者使用当前 `.env` 的同一供应商与 `gpt-5.6-luna`。统一通过仅服务本轮隔离网络的代理调用 Responses API；代理将模型、reasoning effort、输出上限分别固定为该模型、medium、8192，并省略 temperature/top_p、禁用服务端存储。保留原始参数、实际请求与供应商返回用量，凭据只保存在控制端。
- 本机 Docker 出站网络受限，模型请求由内部网络的 TCP relay 经 Unix socket 转发到宿主机 gateway；只有 gateway 可以使用宿主机凭据调用模型，Agent 容器没有外网通路。安装期间的三次 Aster 接入失败（tokenizer 缓存缺失、容器到宿主机 TCP 不通、代理容器不能访问供应商）保存在本地 `setup-history/`，不计入正式题数。
- 三者配置 64000 上下文，保留各自原生提示词、工具和压缩策略。每题最多 900 秒，包含运行结束前的整理；没有额外 token 总量上限，不以工具次数对齐三者。Aster 的参数范围允许至多 500 次模型轮次、500 步工具，实际是否触及另行记录。
- 使用数据集声明的官方题目镜像，三者在同一镜像的独立容器内解题。CPU 4 核、内存 8 GiB，每次最多两题运行，顺序使用种子 20261008 打乱。镜像 ID 和 digest 保存于每次 launch 记录。
- 只提供原始问题、基础源码和公开测试命令说明。容器不挂载答案、评测数据、宿主项目或 Docker socket；网络只能访问模型代理，禁止检索外部解法。先移除题目仓库历史，再建立基础提交；每题会话和记忆为空，不共享题目经验。
- Aster 保留默认记忆和后台整理，使用默认单 Agent。pi 不载入用户插件、skills 与提示模板；Codex 不载入操作者配置，关闭多 Agent 和网络检索。容器环境变量及依赖选择是运行适配，不修改三者生产实现。
- 三者均以无人值守方式授权容器内操作。Aster 启动器为组合 shell 命令接入自动批准回调，原有硬拒绝和 Landlock/seccomp 沙箱仍生效；接入阶段缺少该回调造成的测试阻塞单独留档。
- Aster 的 `execution_environment.py` 选择镜像中的测试 Python，并允许读取该解释器的基础标准库目录；不改写任务逻辑。Codex 挂载完整官方运行包，包括 `codex-code-mode-host`；仅挂载主二进制时的接入失败也单独留档。所有接入尝试见 `setup-attempts.json`，最终三者检查结果见 `smoke-results.json`。
- 初版接入检查只由控制端执行生成的测试，缺少批准回调的 Aster 尝试虽然被记录为测试可过，实际已经超时。正式运行前已加强检查：要求进程正常退出，且 Aster 自身达到 completed；该旧记录原样保留，最终三者均通过新检查。
- 运行前每种 Agent 只做一个不含 benchmark 题目的读写/测试接入检查；如果是安装、协议或环境错误，修复后单列重做，不混入正式题数。正式题目失败后不重新采样。
- 统一导出标准 `instance_id / model_name_or_path / model_patch`，交给未修改的官方评分器和官方镜像，使用不同 run ID，防止命中旧评分缓存。评分结束后按官方 `resolved` 字段统计，补丁为空、服务失败和超时均保留在十题分母。
- 主表报告解决题数、分难度结果、已知 token、缓存 token 和耗时；接口未报告的用量单列。官方镜像/测试基础设施问题单列，不能当成 Agent 能力差异。只进行本轮，不以这些题调参或改进 Aster。

结果名称为“SWE-bench Verified 固定十题子集，官方评分器”。它不构成完整榜单成绩，也不代表对全部仓库或任务难度的统计结论。

## 数据与评分

数据来源是 [SWE-bench/SWE-bench_Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) 的 test split。按 `manifest.json` 中的十个 instance_id 保留原始行，不改动参考补丁、测试补丁、F2P/P2P 或评分脚本。难度来自数据集的专家修复时间：小于 1 小时归为简单、1–4 小时为中等、大于 4 小时为困难。选题沿用[前次清单](../swebench-verified-20261008/manifest.json)，其中有仓库与环境筛选，不能视为从全部 Verified 均匀抽样。

本轮使用 [SWE-bench 官方实现](https://github.com/SWE-bench/SWE-bench/tree/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e)。十个参考补丁已全部通过同一评分器，报告保存在 `environment-checks/`。标准预测文件可以用下列命令评分，三种 Agent 分别使用独立 run ID；本机需在能访问 Docker 的 shell 中执行（当前会话使用 `sg docker`）。

```bash
swebench eval <十行原始数据的subset.json> \
  -p <agent>.predictions.jsonl \
  --run-id comparison-<agent>-20261008 -j 2
```

`compare.py` 负责隔离运行并导出补丁，`summarize.py` 只整理用量和官方报告，不再次调用模型。含参考答案的数据、完整请求响应与 Aster 运行轨迹保存在本地忽略目录 `artifacts/private/swebench-compare-20261008/`；公开文件保留预测、启动参数、用量、工具日志和评分证据。

公开用量保留每次请求的输入、输出、缓存、推理 token 与错误信息；供应商逐消息的 attribution 明细留在本地原始记录，不重复展开到公开文件。
