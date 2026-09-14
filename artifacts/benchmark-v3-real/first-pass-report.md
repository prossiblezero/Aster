# Aster benchmark v3

模式：real；任务内容哈希：`5fe93dc7c85bc5d7219899686d2443491ef88213b518663de0eb5e8af5d2d0eb`。

脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。

| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 全部尝试平均 token |
|---|---:|---:|---:|---:|---:|
| function/all | 12 | 12 | 12/12 | 5.83 | 26250 |
| function/core | 12 | 12 | 12/12 | 6.17 | 23247 |
| memory/memory_off | 12 | 12 | 12/12 | 3.00 | 12531 |
| memory/memory_on | 12 | 12 | 12/12 | 0.17 | 5890 |
| repository/all | 5 | 5 | 5/5 | 16.80 | 50132 |
| repository/core | 5 | 5 | 5/5 | 18.60 | 48197 |

记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。

## 逐题结果

core/all 分别为核心工具集/全部工具；memory_off/on 为关闭/开启记忆。两个条件不合并为一次重试。

| 任务 | 条件 | 完成 | 外部代码测试 | 工具调用 | 总 token | 秒 | 结束原因 |
|---|---|---|---|---:|---:|---:|---|
| humaneval_0 | all | 通过 | 通过 | 5 | 25279 | 26.52 | verified |
| humaneval_0 | core | 通过 | 通过 | 6 | 23068 | 20.34 | verified |
| humaneval_1 | all | 通过 | 通过 | 8 | 32450 | 32.27 | verified |
| humaneval_1 | core | 通过 | 通过 | 5 | 22755 | 26.48 | verified |
| humaneval_10 | all | 通过 | 通过 | 6 | 30917 | 23.20 | verified |
| humaneval_10 | core | 通过 | 通过 | 5 | 22614 | 18.39 | verified |
| humaneval_11 | all | 通过 | 通过 | 5 | 24807 | 54.29 | verified |
| humaneval_11 | core | 通过 | 通过 | 7 | 22750 | 28.49 | verified |
| humaneval_2 | all | 通过 | 通过 | 5 | 24764 | 26.05 | verified |
| humaneval_2 | core | 通过 | 通过 | 9 | 28750 | 31.60 | verified |
| humaneval_3 | all | 通过 | 通过 | 5 | 24975 | 15.88 | verified |
| humaneval_3 | core | 通过 | 通过 | 5 | 22499 | 20.20 | verified |
| humaneval_4 | all | 通过 | 通过 | 5 | 24976 | 24.43 | verified |
| humaneval_4 | core | 通过 | 通过 | 6 | 22685 | 15.63 | verified |
| humaneval_5 | all | 通过 | 通过 | 6 | 25463 | 29.97 | verified |
| humaneval_5 | core | 通过 | 通过 | 6 | 22697 | 17.38 | verified |
| humaneval_6 | all | 通过 | 通过 | 6 | 25345 | 24.75 | verified |
| humaneval_6 | core | 通过 | 通过 | 6 | 23071 | 28.86 | verified |
| humaneval_7 | all | 通过 | 通过 | 7 | 25547 | 23.68 | verified |
| humaneval_7 | core | 通过 | 通过 | 6 | 22625 | 22.57 | verified |
| humaneval_8 | all | 通过 | 通过 | 6 | 25136 | 21.68 | verified |
| humaneval_8 | core | 通过 | 通过 | 6 | 22644 | 23.13 | verified |
| humaneval_9 | all | 通过 | 通过 | 6 | 25347 | 27.65 | verified |
| humaneval_9 | core | 通过 | 通过 | 7 | 22807 | 24.49 | verified |
| memory_api | memory_off | 通过 | 不适用 | 3 | 15405 | 11.27 | verified |
| memory_api | memory_on | 通过 | 不适用 | 1 | 10201 | 6.33 | verified |
| memory_cache | memory_off | 通过 | 不适用 | 1 | 10086 | 8.26 | verified |
| memory_cache | memory_on | 通过 | 不适用 | 0 | 5041 | 3.26 | verified |
| memory_database | memory_off | 通过 | 不适用 | 3 | 15418 | 12.31 | verified |
| memory_database | memory_on | 通过 | 不适用 | 0 | 4997 | 2.79 | verified |
| memory_deploy | memory_off | 通过 | 不适用 | 4 | 10430 | 11.53 | verified |
| memory_deploy | memory_on | 通过 | 不适用 | 0 | 5021 | 2.74 | verified |
| memory_health | memory_off | 通过 | 不适用 | 3 | 15405 | 10.10 | verified |
| memory_health | memory_on | 通过 | 不适用 | 0 | 5020 | 2.14 | verified |
| memory_image | memory_off | 通过 | 不适用 | 4 | 15610 | 11.72 | verified |
| memory_image | memory_on | 通过 | 不适用 | 0 | 4999 | 2.27 | verified |
| memory_logging | memory_off | 通过 | 不适用 | 4 | 10429 | 6.37 | verified |
| memory_logging | memory_on | 通过 | 不适用 | 0 | 5019 | 2.98 | verified |
| memory_release | memory_off | 通过 | 不适用 | 4 | 10415 | 8.96 | verified |
| memory_release | memory_on | 通过 | 不适用 | 1 | 10337 | 8.05 | verified |
| memory_retries | memory_off | 通过 | 不适用 | 3 | 15406 | 14.62 | verified |
| memory_retries | memory_on | 通过 | 不适用 | 0 | 4997 | 3.50 | verified |
| memory_steps | memory_off | 通过 | 不适用 | 1 | 10066 | 5.67 | verified |
| memory_steps | memory_on | 通过 | 不适用 | 0 | 5002 | 4.55 | verified |
| memory_timeout | memory_off | 通过 | 不适用 | 3 | 6288 | 41.69 | verified |
| memory_timeout | memory_on | 通过 | 不适用 | 0 | 5020 | 2.48 | verified |
| memory_worker | memory_off | 通过 | 不适用 | 3 | 15417 | 10.63 | verified |
| memory_worker | memory_on | 通过 | 不适用 | 0 | 5023 | 3.36 | verified |
| add-endpoint-with-test | all | 通过 | 通过 | 10 | 42101 | 40.51 | verified |
| add-endpoint-with-test | core | 通过 | 通过 | 7 | 30479 | 36.24 | verified |
| config-migration | all | 通过 | 通过 | 19 | 44331 | 39.13 | verified |
| config-migration | core | 通过 | 通过 | 25 | 51017 | 61.91 | verified |
| debug-and-fix | all | 通过 | 通过 | 7 | 25895 | 23.55 | verified |
| debug-and-fix | core | 通过 | 通过 | 13 | 50887 | 51.37 | verified |
| dependency-upgrade | all | 通过 | 通过 | 19 | 36151 | 42.37 | verified |
| dependency-upgrade | core | 通过 | 通过 | 20 | 41582 | 40.91 | verified |
| multi-file-refactor | all | 通过 | 通过 | 29 | 102183 | 81.83 | verified |
| multi-file-refactor | core | 通过 | 通过 | 28 | 67021 | 70.50 | verified |

## 失败与边界

- 当前已执行条件未出现任务失败。

题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。
