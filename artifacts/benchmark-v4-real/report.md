# Aster benchmark

模式：real；任务内容哈希：`2ba17e4913c9b34116ed247a48fb86449cb8a96ebf23157bba417f6ce81ead6a`。

脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。

| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 全部尝试平均 token |
|---|---:|---:|---:|---:|---:|
| function/all | 12 | 12 | 12/12 | 6.08 | 27558 |
| function/core | 12 | 12 | 12/12 | 5.83 | 24430 |
| memory/memory_off | 12 | 12 | 11/12 | 2.92 | 13868 |
| memory/memory_on | 12 | 12 | 12/12 | 0.00 | 5084 |
| repository/all | 5 | 5 | 5/5 | 15.00 | 47008 |
| repository/core | 5 | 5 | 5/5 | 15.80 | 41174 |

记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。

## 逐题结果

core/all 分别为核心工具集/全部工具；memory_off/on 为关闭/开启记忆。两个条件不合并为一次重试。

| 任务 | 条件 | 完成 | 外部代码测试 | 工具调用 | 总 token | 秒 | 结束原因 |
|---|---|---|---|---:|---:|---:|---|
| humaneval_0 | all | 通过 | 通过 | 6 | 26715 | 27.12 | verified |
| humaneval_0 | core | 通过 | 通过 | 6 | 23688 | 22.41 | verified |
| humaneval_1 | all | 通过 | 通过 | 7 | 26993 | 25.18 | verified |
| humaneval_1 | core | 通过 | 通过 | 5 | 23339 | 21.80 | verified |
| humaneval_10 | all | 通过 | 通过 | 5 | 26426 | 34.35 | verified |
| humaneval_10 | core | 通过 | 通过 | 7 | 23947 | 22.60 | verified |
| humaneval_11 | all | 通过 | 通过 | 5 | 25943 | 20.53 | verified |
| humaneval_11 | core | 通过 | 通过 | 5 | 22959 | 27.89 | verified |
| humaneval_2 | all | 通过 | 通过 | 6 | 26077 | 23.06 | verified |
| humaneval_2 | core | 通过 | 通过 | 5 | 22806 | 16.86 | verified |
| humaneval_3 | all | 通过 | 通过 | 6 | 26383 | 19.57 | verified |
| humaneval_3 | core | 通过 | 通过 | 5 | 23124 | 19.68 | verified |
| humaneval_4 | all | 通过 | 通过 | 7 | 26653 | 23.06 | verified |
| humaneval_4 | core | 通过 | 通过 | 5 | 23148 | 18.65 | verified |
| humaneval_5 | all | 通过 | 通过 | 7 | 26833 | 22.41 | verified |
| humaneval_5 | core | 通过 | 通过 | 7 | 29773 | 24.72 | verified |
| humaneval_6 | all | 通过 | 通过 | 8 | 34592 | 34.27 | verified |
| humaneval_6 | core | 通过 | 通过 | 8 | 30535 | 32.70 | verified |
| humaneval_7 | all | 通过 | 通过 | 5 | 31753 | 20.85 | verified |
| humaneval_7 | core | 通过 | 通过 | 5 | 23039 | 17.52 | verified |
| humaneval_8 | all | 通过 | 通过 | 5 | 26003 | 18.36 | verified |
| humaneval_8 | core | 通过 | 通过 | 6 | 23366 | 22.71 | verified |
| humaneval_9 | all | 通过 | 通过 | 6 | 26322 | 24.27 | verified |
| humaneval_9 | core | 通过 | 通过 | 6 | 23430 | 20.59 | verified |
| memory_api | memory_off | 通过 | 不适用 | 3 | 15588 | 17.05 | verified |
| memory_api | memory_on | 通过 | 不适用 | 0 | 5102 | 3.14 | verified |
| memory_cache | memory_off | 通过 | 不适用 | 4 | 15741 | 12.26 | verified |
| memory_cache | memory_on | 通过 | 不适用 | 0 | 5080 | 6.04 | verified |
| memory_database | memory_off | 通过 | 不适用 | 3 | 15543 | 11.04 | verified |
| memory_database | memory_on | 通过 | 不适用 | 0 | 5076 | 2.98 | verified |
| memory_deploy | memory_off | 通过 | 不适用 | 3 | 10425 | 13.51 | verified |
| memory_deploy | memory_on | 通过 | 不适用 | 0 | 5096 | 4.67 | verified |
| memory_health | memory_off | 通过 | 不适用 | 3 | 15554 | 13.02 | verified |
| memory_health | memory_on | 通过 | 不适用 | 0 | 5079 | 9.38 | verified |
| memory_image | memory_off | 通过 | 不适用 | 3 | 15602 | 14.00 | verified |
| memory_image | memory_on | 通过 | 不适用 | 0 | 5075 | 3.13 | verified |
| memory_logging | memory_off | 通过 | 不适用 | 4 | 10527 | 30.23 | verified |
| memory_logging | memory_on | 通过 | 不适用 | 0 | 5076 | 5.64 | verified |
| memory_release | memory_off | 通过 | 不适用 | 3 | 15576 | 17.59 | verified |
| memory_release | memory_on | 通过 | 不适用 | 0 | 5085 | 1.91 | verified |
| memory_retries | memory_off | 通过 | 不适用 | 3 | 15566 | 11.56 | verified |
| memory_retries | memory_on | 通过 | 不适用 | 0 | 5075 | 2.38 | verified |
| memory_steps | memory_off | 失败 | 不适用 | 0 | 5165 | 7.41 | verified |
| memory_steps | memory_on | 通过 | 不适用 | 0 | 5079 | 2.08 | verified |
| memory_timeout | memory_off | 通过 | 不适用 | 3 | 15552 | 13.45 | verified |
| memory_timeout | memory_on | 通过 | 不适用 | 0 | 5094 | 3.37 | verified |
| memory_worker | memory_off | 通过 | 不适用 | 3 | 15581 | 11.08 | verified |
| memory_worker | memory_on | 通过 | 不适用 | 0 | 5091 | 5.04 | verified |
| add-endpoint-with-test | all | 通过 | 通过 | 8 | 42202 | 37.28 | verified |
| add-endpoint-with-test | core | 通过 | 通过 | 7 | 31275 | 37.23 | verified |
| config-migration | all | 通过 | 通过 | 17 | 45809 | 40.23 | verified |
| config-migration | core | 通过 | 通过 | 17 | 40244 | 36.46 | verified |
| debug-and-fix | all | 通过 | 通过 | 8 | 33607 | 27.87 | verified |
| debug-and-fix | core | 通过 | 通过 | 7 | 23933 | 22.38 | verified |
| dependency-upgrade | all | 通过 | 通过 | 17 | 37398 | 33.72 | verified |
| dependency-upgrade | core | 通过 | 通过 | 21 | 34369 | 36.66 | verified |
| multi-file-refactor | all | 通过 | 通过 | 25 | 76023 | 63.45 | verified |
| multi-file-refactor | core | 通过 | 通过 | 27 | 76051 | 66.48 | verified |

## 工具结果

按全部尝试统计，安全拒绝和测试断言失败保留各自原因；任务完成不掩盖工具失败。

| 工具 | 调用 | 成功 | 其他结果原因及次数 |
|---|---:|---:|---|
| git_diff | 21 | 21 | — |
| list_files | 44 | 44 | — |
| patch_file | 66 | 60 | fresh_read_required: 6 |
| read_file | 105 | 105 | — |
| run_shell | 12 | 10 | invalid_shell: 2 |
| run_tests | 40 | 40 | — |
| search | 44 | 44 | — |

## 失败与边界

- `memory_steps` / memory_off / attempt 1: answer（verified）

题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。
