# Aster Agent evaluation v1

确定性部分使用 scripted model 和独立 fixture；结果证明 Harness 行为，不代表远程模型成功率。

- Completion contract：3/3（100%）
- Fault injection：4/4（100%）
- Security boundaries：5/5（100%）

- Real model：gpt-5.6-luna
- Pass@1：80.00%
- Pass@3：100.00%
- Verifier pass rate：93.33%
- Avg successful tool steps：4.29
- Avg successful total tokens：14995
- Avg successful elapsed time：14.09s
- Difficulty breakdown：{"easy": {"task_count": 2, "attempt_count": 6, "verifier_pass_rate": 1.0}, "medium": {"task_count": 2, "attempt_count": 6, "verifier_pass_rate": 0.8333333333333334}, "hard": {"task_count": 1, "attempt_count": 3, "verifier_pass_rate": 1.0}}

真实模型结果按独立 fixture、任务、attempt 和 trace 保存；失败原因不隐藏。
