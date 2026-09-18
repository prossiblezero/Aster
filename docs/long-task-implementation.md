# 长任务计划实施记录

2026-09-18 从 `6ee3ed6` 的干净工作区开始，依据 [最新计划](long-task-improvement-plan.md) 第 7–8 节实施。历史网站/Memory 结果仅作问题来源，本页记录本轮验证。

## 第一批：实战可复现问题

- 合法 heredoc 在 Shell 分类阶段先识别复合语法，再对简单命令做 `shlex` 参数解析；复合命令仍需审批，硬拒绝与 NUL 拦截保留。
- required 沙箱仅新增 `/etc/debian_version` 单文件只读权限。禁网离线 pip 构建、安装、安装目录导入成功；系统文件写入及 `/etc/passwd` 读取仍被拒。跨命令依赖复用尚未实现。
- 旧 benchmark 保留原始 usage、缓存/推理及逐指标覆盖率；缺失为未知，部分用量不计算完整成本降幅。
- 显式以被父 Git 忽略的目录启动时，将所选目录作为忽略边界，保留目录内、全局及 info 排除与父仓库已跟踪路径。该特殊模式不继承祖先 `.gitignore`；不创建 `.git`、不修改父 index，原安全规则保持有效。

新增回归先在旧实现复现：Shell/pip 8 失败，Git 3 失败，usage 3 失败。修复后的专项检查均通过；与紧接的工具能力合并运行全量检查，627 项通过（28.45 秒），Ruff 通过。所有 Python 使用 `/home/dzh/.conda/envs/aster/bin/python`。本批为确定性机制验证，未调用真实模型、未修改候选实战项目。

## 第二批：批量补丁与命令输出

`patch_file` 支持 `edits=[{old_text,new_text}, ...]`，旧单补丁参数继续有效。全部片段在同一原文定位，要求唯一且不重叠；任一无效时不发布修改。保留 BOM、未改区域的原始换行、新增文本的首个换行风格，仍通过原有读取新鲜度、审批前版本和隔离发布检查。参考本轮实际读取的 pi [`edit.ts`](https://github.com/earendil-works/pi/blob/46c9de402bddf46b03c3b9f46487b777aaa41861/packages/coding-agent/src/core/tools/edit.ts) 与 [`edit-diff.ts`](https://github.com/earendil-works/pi/blob/46c9de402bddf46b03c3b9f46487b777aaa41861/packages/coding-agent/src/core/tools/edit-diff.ts)；只借鉴原文定位、重叠检查、倒序应用，不采用模糊匹配或移除沙箱。

命令采集上限从静默丢弃 256 KiB 后内容，改为最多 4 MiB 并统计总字节。正常、错误和超时结束的 Shell/测试输出经脱敏归档，模型先收到最多 8,192 字符的预览和 `observation_read` 定位。超限明确报告不可恢复部分。归档失败保留已执行命令的状态和改动事实后停止，不把已发生写入伪装成未执行。取消仍沿用既有进程清理行为，取消瞬间捕获的输出尚不保留。

新增 11 项集成检查覆盖同原文、多种换行/BOM、歧义/重叠、版本竞态/范围、超过旧限额回读、恢复、容量、脱敏和归档失败；纳入本轮 627 项全量通过结果。独立复核发现的 BOM 定位与归档失败记账问题已修复。本批未使用真实模型，未宣称任务效率收益。
