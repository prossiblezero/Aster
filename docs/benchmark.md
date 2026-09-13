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

`--real --repeats 3` 当前使用 3 个独立代码任务，每题运行 3 次，因此总计 9 次尝试。`Pass@1` 看每题第一次是否成功，`Pass@3` 看每题三次中是否至少成功一次；两者不能与 9 次尝试的简单成功比例混为一谈。

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
| Memory dependency | Runtime 中关闭 Memory 与开启 Memory | follow-up 读取次数、任务完成情况 |
| Memory Evolution | 支持、矛盾、伪造证据、snapshot、使用反馈、dream | 证据拒绝、矛盾计数、回滚、反馈和去重 |
| Resume | checkpoint 后继续、workspace 漂移、配置身份变化 | resume 成功率、漂移拒绝率、身份不匹配拒绝率 |
| Stall recovery | repeated、ABAB、output loop | 模式检出率、有界停止 |
| Completion contract | 无编辑、编辑无验证、编辑并验证 | 合同执行率 |

## 四层 Agent 评测

### 任务完成

真实模型任务使用独立 fixture 和文件/pytest verifier。每个任务重复运行时，`Pass@1` 表示第一次就完成，`Pass@3` 表示三次中至少一次完成；二者分别对应用户一次使用的稳定性和模型潜在能力。复杂任务还应按简单、中等、困难分层，而不能只看总平均。

### 效率

每次运行记录工具调用次数、重复调用次数、失败调用次数、输入/输出/总 token 和耗时。完成率提升但工具步数、token 或耗时大幅上升，不应直接认为系统变好。

### 鲁棒性

确定性故障集注入参数错误、命令超时、空/恶意观察和重复失败；分别检查是否识别错误、是否继续使用安全路径、是否恢复或有界停止。后续真实模型实验可以增加固定措辞改写和多次采样，但不能把随机噪声结果与 scripted 合同混写。

### 安全

安全集检查路径逃逸、`.git/.aster/.env/.pem` 等敏感路径、工具结果中的提示注入和权限越界。安全指标单独统计拒绝率与误放行率，不和任务完成率相抵消。

### 评测环境原则

每次运行都创建独立 fixture，运行结束后删除；模型轨迹、工具结果、verifier 和工作区变化写入 trace。确定性评测用于定位 Harness 机制，真实 Luna 评测用于衡量模型在该 Harness 上的实际表现；二者必须分开报告。

## 当前结果

当前版本的模块结果见 `artifacts/module-benchmark-v1.md`，四层结果见 `artifacts/agent-evaluation-v1.md`。确定性部分使用 scripted policy，不调用远程模型；真实部分使用 GPT-5.6 Luna，并保留每一次 attempt 和失败原因。

当前真实评测包含 5 类任务（简单 2 类、中等 2 类、困难 1 类），每类重复 3 次，共 15 次尝试。最新一次结果为 Pass@1 80.00%、Pass@3 100.00%、验证通过率 93.33%；失败样本为 1 次远程网关连接错误。这个样本量适合验证 Harness 流程和发现问题，不能当作大规模模型能力结论。

Memory retrieval 使用固定关键词 query，不能外推为开放域语义检索；当前 Aster 也没有文件新鲜度驱动的陈旧记忆拒绝。真实模型评测需要单独记录模型、采样参数、任务集、重复次数、失败样本和 verifier 结果。

## 评测指标的简历边界

- 可以写：固定任务上的 token 压缩、保留率、Recall@3、重复读取对照、checkpoint 漂移拒绝和 stall 检出率；
- 不能写：开放域 Memory 质量、真实模型成功率、上下文策略自进化收益或陈旧记忆零误召回；
- `10/10` 是基础 Harness regression，模块指标来自 `module-benchmark-v1`，两者必须分开。
