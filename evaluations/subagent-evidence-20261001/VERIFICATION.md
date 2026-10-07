# 实现与离线验证

本轮生产 Python 仍为 55 个文件，从 11,827 行增加到 11,907 行，净增 80 行（约 0.68%），零新增运行依赖。只改动 Runtime、WorkerManager 和提示文本；没有新增执行引擎、配置项或工具。逐文件冻结信息见 FREEZE.json，变更内容见 candidate.patch，计数见 code-metrics.json。

## 实现边界

- read_file 给模型的输出带来源元数据和逐行行号；直接工具 Outcome.content 仍是原始文本。
- 子任务交付复用既有工作记忆，最多四段短源码摘录，带哈希、行段和证据 ID。敏感内容在截断前脱敏，过期摘录不能作为新的父方有效观察。
- 模型接收当前摘录可以满足观察门槛。界面轮询不算接收；子任务文件哈希不转移为父任务编辑凭证，也不替代父任务测试。
- 快照恢复拿当时的摘录，保留已有父子分支隔离。预算不足的续问不会创建新运行覆盖旧记录。
- 普通自主子任务也阻止父方过早结束。父模型请求期间到达的新交付先进入后续观察，再做最终整合；显式取消与预算停止仍能清理孩子。

以上保证工程边界，不保证子模型结论正确、短摘录覆盖所有引用或父模型不再主动重读。

## 回归

使用 /home/dzh/.conda/envs/aster/bin/python（Python 3.12），显式 PYTHONPATH=src：

- 完整回归：**1,087 passed，4 skipped**，105.53 秒。
- 子任务/UI、生命周期、输出与并行读取相关检查：79 passed。
- 4K 上下文归档及子任务聚焦：65 passed。初版增加的常驻提示触发小窗口边界；缩短提示并将子任务描述按能力启用后通过，未放大测试预算。
- Ruff、git diff --check 通过。源码与冻结 candidate 的 55 个文件哈希一致。
- 第 13 章可运行示例通过；教学站重新生成后，网站全套 25 项测试通过（1.51 秒）。
- 新增七个参数化测试案例，覆盖源码摘录与行号、严格观察门槛下零父读取完成、界面轮询、旧快照和过期文件、编辑权限、提前 Final/迟到交付、token 不足续问。其余变动同步已有测试中的可见行号和显式停止契约。

## wheel

构建 wheel 后，安装到独立目录 /tmp/aster-evidence-wheel-20261001，从 /tmp 启动测试，预先断言 aster.__file__ 位于该安装目录：

- 55 个生产 Python 文件在 checkout、wheel、安装目录中逐字节一致。
- 安装包的 Worker/UI/编辑前读取检查：**55 passed**，18.07 秒。
- wheel SHA-256：6fc398557b4953cb18f4a8da18fc755540fedde3a32a0de9f77efb6e38c1e749。
- 机器可读信息见 WHEEL-VERIFICATION.json；wheel 位于忽略目录 artifacts/private/subagent-evidence-20261001/wheel。

这是独立安装路径验证，依赖仍来自同一个 Conda 环境，不声明另一个操作系统或完整新环境的兼容性。未改变共享开发环境中的 editable 安装。

## 真实模型评测

与上述离线验证分别报告。按 PROTOCOL.md 固定六个已见任务、旧 Parallel/新 Solo/新 Parallel 三组、18 个首次运行；运行前冻结源代码、驱动、评分器和配置。原 subagent-light-20260930 数据和报告保持原样。结果输出与统计见本目录 results.json、summary.json 和 REPORT.md。
