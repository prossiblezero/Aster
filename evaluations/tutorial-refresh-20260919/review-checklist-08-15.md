# 第 08–15 章独立验收清单

日期：2026-09-19。基于当前源码及 `evaluations/lab-followup-20260919/REPORT.md` 的只读审查；以下是验收要求，尚不表示 Aster 候选已通过。本文不修改教程、产品或冻结证据。

## 必须修正

- [ ] **09 检查点与恢复**：撤换过时的“真实入口节选”与无条件拒绝流程；说明 `session.load_checkpoint`、显式 `allow_model_change` / `adopt_workspace` 及安全配置仍需一致。区分加载旧运行与 continuation 建立新 run：后者重置 step、turn、usage，不能笼统写“恢复后不清零”。补 checkpoint v2、祖先归档来源、模型历史转换、`inflight.json` 对不确定工具副作用的拒绝；`adopt_workspace` 不能绕过。在复用环境下恢复不恢复依赖环境，正式验证需失效重做。依据：`src/aster/runtime.py` 的 `resume`、`checkpoint`；`src/aster/session.py` 的 `load_checkpoint`、`prepare_continuation`、`assert_settled_tools`。
- [ ] **12 Skills**：说明同时支持 `<name>.md` 与 `<name>/SKILL.md`，包资源和含 description 等元信息的目录；删除“不是 SKILL.md”旧结论。正文选中后保存在 `skill_snapshots`，跨轮及分页使用固定版本，显式 `refresh=True` 才重新加载；`resource` 读取资源但不激活正文，不能与 refresh 同用。更新旧源码片段，实验应展示旧快照保持与显式刷新。分页默认 512、最大 1000 仍正确。依据：`src/aster/skills.py`、`src/aster/tools.py` 的 `SkillLoad`、`src/aster/runtime.py` 的 `skill_load` 分支。
- [ ] **13 会话操作**：补 `/model [alias]`、`/new`、`/continue [--adopt-workspace]`、`/resume RUN_ID [--allow-model-change] [--adopt-workspace]`。模型切换基于预配置 profile，在空闲边界校验历史转换与新预算后安装设置；不能暗示任意型号字符串均已接通。解释流式预览和失败尝试的边界，完整响应确认前不能执行工具参数。依据：`src/aster/tui.py`、`src/aster/runtime.py` 的 `switch_model`。
- [ ] **15 数据合同与训练算法**：`context_policy` 更新为 `deterministic-v7`。保留“尚未实现 SFT/GRPO 训练算法”；补 `training.py` 已有的任务来源/许可证及划分、实际请求与回复匹配导出、assistant mask、行为/参考 logprobs、版本/预算与 GRPO 分组校验。不能把这些结构和校验称为训练器。区分环境演示奖励与训练资格：训练 rollout 的取消、基础设施或评分器错误必须保持 `reward=None`。依据：`src/aster/context.py`、`src/aster/rl.py`、`src/aster/training.py`。

## 补充与保留

- [ ] **14 最新评测证据**：补 v0.2-release 与 lab-followup 入口，用已发现反例说明独立评分器也需验证。当前旧历史候选 22/24；A 返修产品 24/24 但正式验证缺失；B 原产物 20/24，另加框架修复、明确反馈及额外预算后才 24/24 且 verified。追加成功不能改写为原预算内成功、prompt 因果收益或未见任务泛化。未知 usage 不按零计；缓存率不能代替交付质量和总成本；已用开发题不能转充未见测试。
- [ ] **10 Memory**：本章明确 `memory_enabled=False` 默认关闭，短期工作记忆不受此开关影响。更新源码归属：文件观察采集在 `_collect_step`，出处认证已移到 `memory.store_excerpt`。保留“当前运行成功 read_file 的原文摘录、来源变化失效、拒绝无证据自由总结”，不得把它写成自动经验总结。
- [ ] **11 Dream**：保留三个开关默认 False、测试反馈是粗粒度关联、Dream 仅隔离已有记录而不生成知识或训练模型。说明只开 auto_dream 不足：Memory/evolution 关闭时返回 skipped；不把示范性注入反馈当作真实效果证据。
- [ ] **08 正式完成验证**：明确成功的 run_shell 不能更新正式验证，需通过 run_tests；沿用当前完成边界和失败反馈。无需为更新而重写准确的 Hooks 说明。
- [ ] **13 Worker 边界**：保留默认 max_workers=0、独立 12 步/12 轮、禁止递归派生、共享工作区等准确说明；Worker 的持久 Memory 召回也关闭，不能仅写关闭写入。不要把父 run 预算称为团队累计预算。依据：`src/aster/workers.py`。

## 交付检查

- [ ] Markdown、展示的源码片段、可运行实验一致，网站从最终 Markdown 重新生成。
- [ ] 教程实验由操作员在原生 `aster` 环境独立复验；候选运行环境问题不能通过屏蔽 tiktoken、降低 required 沙箱要求或改弱教学合同来掩盖。
- [ ] 记录候选实际覆盖和未解决项；不把“有对应关键字”当作行为说明已正确。
