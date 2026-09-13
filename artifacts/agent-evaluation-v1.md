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
- Difficulty breakdown（效率只统计成功运行）：
  - easy：6/6 verifier pass；2.83 tool steps、10490 tokens、10.91s
  - medium：5/6 verifier pass；4.80 tool steps、17372 tokens、15.76s
  - hard：3/3 verifier pass；6.33 tool steps、20043 tokens、17.65s

真实模型结果按独立 fixture、任务、attempt 和 trace 保存；失败原因不隐藏。
