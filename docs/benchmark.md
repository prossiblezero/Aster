# Aster 模块级 Benchmark

运行命令：

```bash
conda activate aster
PYTHONPATH=src:scripts python scripts/benchmark_modules.py
```

脚本生成：

- `artifacts/module-benchmark-v1.json`：机器可读原始结果；
- `artifacts/module-benchmark-v1.md`：人类可读摘要；
- `artifacts/deterministic-evaluation.json`：基础 Harness regression。

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

## 当前结果

当前版本的确定性结果见 `artifacts/module-benchmark-v1.md`。结果使用 scripted deterministic policy，不调用远程模型；因此它们证明 Harness 机制的可验证行为，不代表 GPT-5.6 Luna 的任务成功率。

Memory retrieval 使用固定关键词 query，不能外推为开放域语义检索；当前 Aster 也没有文件新鲜度驱动的陈旧记忆拒绝。真实模型评测需要单独记录模型、采样参数、任务集、重复次数、失败样本和 verifier 结果。

## 评测指标的简历边界

- 可以写：固定任务上的 token 压缩、保留率、Recall@3、重复读取对照、checkpoint 漂移拒绝和 stall 检出率；
- 不能写：开放域 Memory 质量、真实模型成功率、上下文策略自进化收益或陈旧记忆零误召回；
- `10/10` 是基础 Harness regression，模块指标来自 `module-benchmark-v1`，两者必须分开。
