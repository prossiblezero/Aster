# Aster benchmark

模式：scripted；任务内容哈希：`8ff09689f071a835517fab9f3f8a53f988b429c3e2eb2eddd828bb0eda662521`。

脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。

| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 全部尝试平均 token |
|---|---:|---:|---:|---:|---:|
| function/core | 164 | 164 | 164/164 | 3.00 | 0 |
| harness/core | 12 | 12 | 12/12 | 2.33 | 0 |
| memory/memory_off | 12 | 12 | 12/12 | 1.00 | 0 |
| memory/memory_on | 12 | 12 | 12/12 | 0.00 | 0 |
| repository/core | 5 | 5 | 5/5 | 10.40 | 0 |

记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。

验证器反向检查：169/169 原始版本未通过目标测试，且原有行为测试通过。参考修复的结果见 scripted 行。

上下文配置 12 组：含工具定义的输入平均 18267 → 3412 tokens；预算内 12/12；当前请求保留 12/12。
这些 token 是本地 tokenizer 对请求 JSON 的估计值，实际计费以远程 usage 为准。

## 工具结果

按全部尝试统计，安全拒绝和测试断言失败保留各自原因；任务完成不掩盖工具失败。

| 工具 | 调用 | 成功 | 其他结果原因及次数 |
|---|---:|---:|---|
| memory_store | 4 | 2 | sensitive: 1, transient: 1 |
| patch_file | 187 | 186 | invalid_arguments: 1 |
| read_file | 216 | 215 | BoundaryError: 1 |
| run_shell | 5 | 4 | error: 1 |
| run_tests | 172 | 172 | — |

## 失败与边界

- 当前已执行条件未出现任务失败。

题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。
