# 子任务证据交付开发对照（2026-10-01）

在首次付费调用前冻结。复用 2026-09-30 的六道公开任务、行为 oracle 和 AST 引用评分器，不修改历史报告、不挑题、不修补模型回答。每题一次独立 pass@1，三组共 18 次：改动前 Parallel、本次 Solo、本次 Parallel。六种组内顺序各用一次，逐回合串行执行；Parallel 回合中的两个真实子 Runtime 并发。开发集已见，不是 holdout，也不代表原 benchmark 官方成绩。

三个条件使用同一已配置模型、完整请求、工具、输出契约和团队预算：36 模型请求、60 工具、300,000 已报告 token、600 秒。相同源快照、任务准备、完整验收及团队预算。保留模型服务正常的有界重试；没有失败后择优重跑。服务失败计入成绩，未知 usage 单列。父任务最终测试门槛不放宽；本组只读调查任务没有编辑/测试要求。

旧版从当前 Git HEAD 的生产源码复制，新版从当前 checkout 复制；两个源码目录均通过逐文件 SHA-256 冻结，实际子进程必须导入对应目录，运行前后再次校验。驱动、原评分器、数据、配置身份与服务端点哈希也冻结。源码快照及请求轨迹保存在被忽略的 artifacts/private/subagent-evidence-20261001；FREEZE.json 和 candidate.patch 公开保存复现信息。API 密钥不进入公开输出。

主指标为严格整题通过、严格事实正确数、总耗时、全队已报告 token；诊断指标为主代理输入、父读取与父子路径重叠、子任务交付、HTTP 未知 usage。按任务比较两组 Parallel；新版 Solo 用来区分行号显示修复和委派本身的效果。单次小样本不作统计显著性或普遍能力结论。离线源码摘录、权限、快照及生命周期检查与真实模型成绩分别报告。

命令（Conda aster Python 3.12）：

```bash
python evaluations/subagent-evidence-20261001/evaluate.py freeze
python evaluations/subagent-evidence-20261001/evaluate.py run
python evaluations/subagent-evidence-20261001/evaluate.py summarize
```
