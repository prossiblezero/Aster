# 实现与离线验证

本轮在 2026-10-01 已评测 candidate 上增量修改八个生产文件，净增 86 行，仍为 55 个 Python 文件，零新增运行依赖。调用前冻结信息在 FREEZE.json，增量在 candidate.patch。

## 工程边界

`worker_finish` 仅对子任务可见，处理模型已经读过的来源引用；原文取自既有 EvidenceStore，不由模型重新抄写。原有原子快照保存证据 ID 对应运行的索引及最终短摘录。与仅检查工具调用 ID 相比，这可拒绝旧分支恢复后引用未来分支的读取，即使另一个调用重用了该工具调用 ID。

交付最多四段、总计 4,000 源码字符；支持较早的读取及同一路径的多个位置。范围或版本不匹配、超量、来源不属于该分支时整次提交失败，不安装部分选择。结论标识只用于关联，不作为语义正确证明。

完成仍进入 Runtime.finalize 的原有门槛。工具调用结束任务后无需额外模型请求；它占用一个正常工具额度。JSON 对象由标准库序列化；纯文本、旧子快照和预算耗尽交付保持回退行为。续问清空上一轮选择，仍可以引用内容与当前文件相符的历史读取。

父模型得到有效摘录可以满足观察门槛；UI 轮询不会消费证据。摘录不授予父方编辑凭证，也不替代父方测试。过期选中片段仍作为历史交付保留，同时在 stale_paths 标记来源变化。

## 测试

使用 `/home/dzh/.conda/envs/aster/bin/python`，Python 3.12，源码验证显式 `PYTHONPATH=src`：

- 最终完整回归：**1,098 passed，4 skipped**，151.21 秒。
- 精选交付、工具契约聚焦：111 passed，18.62 秒；最终完整回归包含随后追加的测试完成门槛参数。
- Context、旧回归及交付检查：44 passed，8.09 秒。
- 新增 tests/test_worker_handoff.py，11 个案例；同步已有工具目录测试中的子任务专用工具可见性。
- Ruff 与 git diff --check 通过。
- 第 13 章现有可运行示例通过；教学站点重建后 **25 passed**，1.96 秒。

初次全量检查发现一项工具目录断言需要更新：主任务不能看到子任务专用交付工具。修正为按父子角色暴露工具，未扩大普通主任务的核心工具集合。最终全量结果采用修正后的代码。

## 安装包

通过 pyproject 声明的构建后端生成 wheel，安装到 `/tmp/aster-selected-wheel-20261002`，从 `/tmp` 启动，并断言 `aster.__file__` 确实来自该目录。构建采用 pip 的标准隔离构建；未向项目增加依赖。

- 55 个生产文件在 checkout、wheel、安装目录中逐字节一致。
- 安装路径下运行 tests/test_worker_handoff.py、tests/test_interaction_workers.py、tests/test_tool_contracts.py：**163 passed**，38.70 秒。
- wheel SHA-256：`74cc34d0aa477876a1595708af6ffd383f3da761f37ba7517b41d612e53cafc6`。
- wheel 和原始运行记录位于忽略目录 artifacts/private/subagent-selected-20261002；公开摘要在 WHEEL-VERIFICATION.json。

这是独立安装路径验证，依赖仍来自同一 Conda 环境，不是另一操作系统或全新依赖环境的兼容性验证。

## 模型评测限制

24 个首次回合全部未获得有效模型答案。51 个尝试被服务以 401 拒绝，3 个随父任务失败而取消，54 个尝试均无 total usage。没有将失败样本删除、替换或修复后补分。详细协议、冻结版本和服务失败诊断见同目录 REPORT.md、PROTOCOL.md、diagnostics.json。

当前生产源码与已尝试运行的冻结 candidate 一致。真实模型重读、正确率、token 及调度收益尚无本轮证据，不能由离线通过数推断。
