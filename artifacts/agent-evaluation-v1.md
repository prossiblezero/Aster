> 历史报告：其中的旧百分比和 token 数字不再作为当前成果依据。请使用 [v2 评测说明](../docs/benchmark.md) 和 v2 报告。

# Aster Agent evaluation v1

确定性部分使用 scripted model 和独立 fixture；结果证明 Harness 行为，不代表远程模型成功率。

- Completion contract：3/3（100%）
- Harness regression：10/10（100%）
- Fault injection：4/4（100%）
- Security boundaries：5/5（100%）

- Real model：gpt-5.6-luna
- Pass@1：100.00%
- Pass@3：100.00%
- Verifier pass rate：100.00%
- Avg successful tool steps：5.13
- Avg successful total tokens：14887
- Avg successful elapsed time：54.04s
- Difficulty breakdown (successful attempts only for efficiency):
  - easy: 6/6 verifier pass; 2.83 tool steps, 10553 tokens, 42.51s
  - medium: 6/6 verifier pass; 5.83 tool steps, 17147 tokens, 57.41s
  - hard: 3/3 verifier pass; 8.33 tool steps, 19034 tokens, 70.36s

真实模型结果按独立 fixture、任务、attempt 和 trace 保存；失败原因不隐藏。
