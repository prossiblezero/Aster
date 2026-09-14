# Aster 模块级 Benchmark

运行命令：

```bash
conda activate aster
PYTHONPATH=src:scripts python scripts/benchmark_modules.py
```

按教程中的“完成、效率、鲁棒性、安全”四层运行 Agent 评测：

```bash
PYTHONPATH=src:scripts python scripts/agent_eval.py
PYTHONPATH=src:scripts python scripts/agent_eval.py --real --repeats 3 --env-file .env
```

`--real --repeats 3` 当前使用 5 个独立代码任务，每题运行 3 次，因此总计 15 次尝试。`Pass@1` 看每题第一次是否成功，`Pass@3` 看每题三次中是否至少成功一次；两者不能与 15 次尝试的简单成功比例混为一谈。效率按简单、中等、困难分层，只在成功运行内计算工具步数、token 和耗时，避免用一个平均数掩盖任务差异。

脚本生成：

- `artifacts/module-benchmark-v1.json`：机器可读原始结果；
- `artifacts/module-benchmark-v1.md`：人类可读摘要；
- `artifacts/deterministic-evaluation.json`：基础 Harness regression。
- `artifacts/agent-evaluation-v1.json`：四层评测和真实模型逐次结果；
- `artifacts/agent-evaluation-v1.md`：四层评测摘要。

## 为什么要拆模块评测

Harness 的总通过率只能证明边界合同没有明显回归，不能证明 context、Memory 或 recovery 真的有收益。因此 Aster 把评测拆成固定任务、对照和物理证据：

| 模块 | 对照 | 主要指标 |
| --- | --- | --- |
| Context | 大预算、不 compact 的同一组消息 | token 压缩率、预算内率、当前请求保留率、重要证据保留率 |
| Memory retrieval | 固定 lesson 与干扰项 | Recall@3、MRR、Precision@3、无关项比例 |
| Memory dependency | Runtime 中关闭 durable memory 与预置 durable lesson | 跨运行 follow-up 读取次数、任务完成情况 |
| Session working memory | 首轮观察后清空原始观察，不注入 working memory | 摘要记录率、下一轮事实注入率、修改后失效率 |
| Memory Evolution | 支持、矛盾、伪造证据、snapshot、使用反馈、dream | 证据拒绝、矛盾计数、回滚、反馈和去重 |
| Resume | checkpoint 后继续、workspace 漂移、配置身份变化 | resume 成功率、漂移拒绝率、身份不匹配拒绝率 |
| Stall recovery | repeated、ABAB、output loop | 模式检出率、有界停止 |
| Completion contract | 无编辑、编辑无验证、编辑并验证 | 合同执行率 |

当前确定性对照结果如下。这里的“基线”只在该模块关闭或使用大预算，其余输入、任务和验证器保持一致；因此可以说明机制的直接收益，但不能推导端到端真实模型提升。

| 机制 | 基线 | Aster | 结果含义 |
| --- | --- | --- | --- |
| 上下文压缩 | 大预算、不压缩：平均 708.9 tokens | 平均 581.9 tokens | 减少 11.23%，当前请求和验证证据保留率 100% |
| 跨运行记忆依赖 | 关闭 durable memory：12 次重复读文件 | 预置 durable lesson：0 次 | 重复读取减少 100%，两组任务均完成 |
| 记忆召回 | 12 条固定事实 + 干扰项 | 目标事实均排第 1 | Recall@3、MRR、Top-3 precision 均 100% |
| 会话工作记忆 | 清空原始观察且不注入摘要 | 读取后摘要记录、下一轮事实注入均成功 | 1/1 固定场景通过；修改后摘要失效 |
| 任务恢复 | 漂移/身份变化作为拒绝场景 | 正常恢复并拒绝不安全恢复 | 10/10 场景通过 |
| 完成判定 | 9 个正反合同场景 | 严格检查编辑、验证和验证新鲜度 | 9/9 场景通过 |

## 四层 Agent 评测

### 任务完成

真实模型任务使用独立 fixture 和文件/pytest verifier。每个任务重复运行时，`Pass@1` 表示第一次就完成，`Pass@3` 表示三次中至少一次完成；二者分别对应用户一次使用的稳定性和在当前任务集上的可达成功率。报告同时列出每个难度层的通过次数，以及成功运行的工具步数、token 和耗时；这比把读文件任务和多文件改造任务混成一个平均数更容易解释。当前任务集只有 5 类、每类 3 次，结果用于验证 Harness 流程和发现问题，不代表通用模型能力。

### 效率

每次运行记录工具调用次数、重复调用次数、失败调用次数、输入/输出/总 token 和耗时；结果按难度分层，并保留失败样本。完成率提升但工具步数、token 或耗时大幅上升，不应直接认为系统变好。当前 Luna 成功运行的分层结果为：简单 **2.83 步、10,490 token、10.91 秒**；中等 **4.80 步、17,372 token、15.76 秒**；困难 **6.33 步、20,043 token、17.65 秒**。这些数值与任务复杂度单调增加，不能理解为简单任务需要 6 次工具调用。

### 鲁棒性

确定性故障集注入参数错误、命令超时、空/恶意观察和重复失败；分别检查是否识别错误、是否继续使用安全路径、是否恢复或有界停止。后续真实模型实验可以增加固定措辞改写和多次采样，但不能把随机噪声结果与 scripted 合同混写。

### 安全

安全集检查路径逃逸、`.git/.aster/.env/.pem` 等敏感路径、工具结果中的提示注入和权限越界。安全指标单独统计拒绝率与误放行率，不和任务完成率相抵消。

### 评测环境原则

每次运行都创建独立 fixture，运行结束后删除；模型轨迹、工具结果、verifier 和工作区变化写入 trace。确定性评测用于定位 Harness 机制，真实 Luna 评测用于衡量模型在该 Harness 上的实际表现；二者必须分开报告。

## 当前结果

当前版本的模块结果见 `artifacts/module-benchmark-v1.md`，四层结果见 `artifacts/agent-evaluation-v1.md`。确定性部分使用 scripted policy，不调用远程模型；真实部分使用 GPT-5.6 Luna，并保留每一次 attempt 和失败原因。

当前真实评测包含 5 类任务（简单 2 类、中等 2 类、困难 1 类），每类重复 3 次，共 15 次尝试。修复消息去重和接入 working memory 后，最新结果为 **Pass@1 100%、Pass@3 100%、验证通过率 100%**；按难度为：简单 6/6，中等 6/6，困难 3/3。成功运行效率按难度单独统计；本次样本量适合验证 Harness 流程和发现问题，不能当作大规模模型能力结论。

Memory retrieval 使用固定关键词 query，不能外推为开放域语义检索；当前 Aster 也没有文件新鲜度驱动的陈旧记忆拒绝。真实模型评测需要单独记录模型、采样参数、任务集、重复次数、失败样本和 verifier 结果。

## 评测指标的简历边界

- 可以写：固定任务上的 token 压缩、保留率、Recall@3、重复读取对照、checkpoint 漂移拒绝和 stall 检出率；
- 不能写：开放域 Memory 质量、真实模型成功率、上下文策略自进化收益或陈旧记忆零误召回；
- `10/10` 是基础 Harness regression，模块指标来自 `module-benchmark-v1`，两者必须分开。
