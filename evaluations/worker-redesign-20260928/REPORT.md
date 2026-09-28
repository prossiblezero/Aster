# Worker 重设计三臂评测报告

本报告记录 2026-09-28 冻结的三臂真实模型评测。每道题运行一次
`solo`、`handoff_only` 和 `redesigned`，使用同一模型、提示、初始工作区、
评分器和外部团队预算。数据集包含 20 道回归题和 6 道预先冻结的 holdout
题，来源包括 Aider、BigCodeBench、SWE-bench Verified 和 Terminal-Bench。

评测使用 Conda `aster` 环境和 `gpt-5.6-luna`，共 26 道题、78 次运行；三臂
输入检查、源码绑定、初始工作区哈希和用量账本均通过。完整审计见
[RESULTS-AUDIT.md](RESULTS-AUDIT.md)，实际交付轨迹见
[SIMPLE-DELIVERY-AUDIT.md](SIMPLE-DELIVERY-AUDIT.md)、
[MEDIUM-DELIVERY-AUDIT.md](MEDIUM-DELIVERY-AUDIT.md)、
[TERMINAL-DELIVERY-AUDIT.md](TERMINAL-DELIVERY-AUDIT.md) 和
[MECHANISM-AUDIT.md](MECHANISM-AUDIT.md)。

## 结果

| 臂 | 任务通过 | Runtime 正常完成 | 全队 tokens | 秒数 | Worker 启动/完成 | 持久化交接 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Solo | 17/26 (65.4%) | 14/26 | 10,303,109 | 7,188.4 | 0/0 | 0 |
| Handoff only | 15/26 (57.7%) | 16/26 | 12,513,269 | 7,667.1 | 23/7 | 23 |
| Redesigned | 15/26 (57.7%) | 17/26 | 11,204,563 | 8,302.6 | 21/8 | 21 |

按评测划分：

| 划分 | Solo | Handoff only | Redesigned |
| --- | ---: | ---: | ---: |
| 回归 20 题 | 13/20 | 13/20 | 12/20 |
| Holdout 6 题 | 4/6 | 2/6 | 3/6 |

回归题上，Redesigned 比 Solo 少 1 个通过任务；holdout 上比 Solo 少 1 个，
比 Handoff only 多 1 个。三臂各只采样一次，不能从这些差异推断稳定因果收益。
Redesigned 的全队 token 比 Solo 多约 8.7%，总时长多约 15.5%；因此本次数据
不支持“委派降低总成本”或“委派提高总体通过率”。

## 实际观察到的行为

- 新设计确实保存了失败、取消和额度耗尽边界的 partial handoff；三臂合计
  Redesigned 保存 21 份交接，其中 13 份为 partial，4 份自动送达父请求。
  这证明状态交付的可靠性改进，但交接本身不是任务成功。
- 前三道中等题的 5 个子任务交付都进入父模型上下文，但没有证据表明它们
  提升了独立评分。Django 案例中，Worker 因父任务没有传入原始复现条件而
  漏掉 SMTP 路径，说明上下文完整性比“报告已送达”更关键。
- `terminal-cobol-modernization` 是最清楚的正向协作案例：handoff-only 的
  Worker 在父读取源码前提供了固定宽度和更新语义地图，父随后引用了这些
  信息；但父仍重读全部来源。Redesigned 版本的 Worker 与父实现重叠，且把
  声明记录宽度误报成实测输入长度，因此不能把这个单例推广为总体收益。
- `holdout-sympy-13878` 中 Redesigned 通过而 Handoff only 未通过，构成一
  个配对胜例；Solo 同样通过，且只是一次采样，不能证明胜因来自 Worker。
- 简单控制题均通过，很多运行没有启动 Worker。这说明系统不会强制拆分每个
  请求，但开启 Worker 仍会带来额外协调和上下文成本。

## 账本与限制

78/78 个结果的 request ledger 与结果用量一致，HTTP ledger reconciliation
也全部通过；1,686 次 HTTP 尝试中 1,673 次带数值 usage，剩余请求处于传输、
取消或不完整响应边界，不能换算成精确账单。所有 episode 的源码未被评分器改动。

评分是本地适配的独立测试，不是官方 benchmark leaderboard 分数。部分任务在
外部 token、墙钟或供应商响应边界停止；报告保留这些终态，并把代码评分、Runtime
正常结束和 Worker 交付分别统计。首个 paid episode 与本地 pytest/wheel 验证有
约 31.6 秒主机负载重叠，已保留且未重跑。

本轮可以对外说明的工程结果是：Aster 在约 3.2% 的生产代码增量内实现了
有界额度、持久化 partial handoff、失败恢复和单写入者约束；真实任务评测证明
这些交付路径可观测、可对账，但尚未证明 Worker 协作提高任务通过率或降低全队
成本。后续若继续改进，应优先解决任务上下文传递、父子重复调查和测试入口适配，
再进行新的冻结对照，而不是把本轮较短的失败运行写成效率收益。
