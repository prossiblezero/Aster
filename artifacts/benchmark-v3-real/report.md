# Aster benchmark v3

模式：real；任务内容哈希：`2691e84bfe9be272b1f5784fc0f6a66529a940d9e3f9f7a45013601eb119d136`。

脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。

| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 全部尝试平均 token |
|---|---:|---:|---:|---:|---:|
| function/all | 12 | 12 | 12/12 | 5.92 | 27783 |
| function/core | 12 | 12 | 12/12 | 6.67 | 25780 |
| memory/memory_off | 12 | 12 | 11/12 | 3.08 | 13671 |
| memory/memory_on | 12 | 12 | 12/12 | 0.00 | 5159 |
| repository/all | 5 | 5 | 5/5 | 15.80 | 47963 |
| repository/core | 5 | 5 | 5/5 | 18.20 | 45706 |

记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。

## 逐题结果

core/all 分别为核心工具集/全部工具；memory_off/on 为关闭/开启记忆。两个条件不合并为一次重试。

| 任务 | 条件 | 完成 | 外部代码测试 | 工具调用 | 总 token | 秒 | 结束原因 |
|---|---|---|---|---:|---:|---:|---|
| humaneval_0 | all | 通过 | 通过 | 5 | 25757 | 18.43 | verified |
| humaneval_0 | core | 通过 | 通过 | 8 | 29679 | 24.01 | verified |
| humaneval_1 | all | 通过 | 通过 | 5 | 25710 | 19.37 | verified |
| humaneval_1 | core | 通过 | 通过 | 5 | 23338 | 27.29 | verified |
| humaneval_10 | all | 通过 | 通过 | 9 | 33725 | 30.88 | verified |
| humaneval_10 | core | 通过 | 通过 | 7 | 23797 | 31.43 | verified |
| humaneval_11 | all | 通过 | 通过 | 6 | 25704 | 25.81 | verified |
| humaneval_11 | core | 通过 | 通过 | 6 | 22964 | 21.22 | verified |
| humaneval_2 | all | 通过 | 通过 | 5 | 25265 | 27.08 | verified |
| humaneval_2 | core | 通过 | 通过 | 6 | 22904 | 22.96 | verified |
| humaneval_3 | all | 通过 | 通过 | 5 | 31080 | 37.34 | verified |
| humaneval_3 | core | 通过 | 通过 | 8 | 29493 | 29.92 | verified |
| humaneval_4 | all | 通过 | 通过 | 6 | 26140 | 20.90 | verified |
| humaneval_4 | core | 通过 | 通过 | 5 | 22941 | 27.32 | verified |
| humaneval_5 | all | 通过 | 通过 | 6 | 31416 | 25.95 | verified |
| humaneval_5 | core | 通过 | 通过 | 6 | 23262 | 17.67 | verified |
| humaneval_6 | all | 通过 | 通过 | 5 | 25663 | 20.04 | verified |
| humaneval_6 | core | 通过 | 通过 | 9 | 30116 | 34.59 | verified |
| humaneval_7 | all | 通过 | 通过 | 7 | 25646 | 36.39 | verified |
| humaneval_7 | core | 通过 | 通过 | 8 | 29167 | 24.27 | verified |
| humaneval_8 | all | 通过 | 通过 | 7 | 26114 | 23.02 | verified |
| humaneval_8 | core | 通过 | 通过 | 6 | 28226 | 19.52 | verified |
| humaneval_9 | all | 通过 | 通过 | 5 | 31178 | 22.84 | verified |
| humaneval_9 | core | 通过 | 通过 | 6 | 23479 | 24.09 | verified |
| memory_api | memory_off | 通过 | 不适用 | 4 | 16055 | 11.25 | verified |
| memory_api | memory_on | 通过 | 不适用 | 0 | 5172 | 2.53 | verified |
| memory_cache | memory_off | 通过 | 不适用 | 5 | 16099 | 15.97 | verified |
| memory_cache | memory_on | 通过 | 不适用 | 0 | 5161 | 4.10 | verified |
| memory_database | memory_off | 通过 | 不适用 | 3 | 15795 | 12.97 | verified |
| memory_database | memory_on | 通过 | 不适用 | 0 | 5166 | 3.72 | verified |
| memory_deploy | memory_off | 通过 | 不适用 | 3 | 15796 | 8.88 | verified |
| memory_deploy | memory_on | 通过 | 不适用 | 0 | 5161 | 2.24 | verified |
| memory_health | memory_off | 通过 | 不适用 | 2 | 10521 | 9.79 | verified |
| memory_health | memory_on | 通过 | 不适用 | 0 | 5157 | 5.63 | verified |
| memory_image | memory_off | 通过 | 不适用 | 3 | 15797 | 15.27 | verified |
| memory_image | memory_on | 通过 | 不适用 | 0 | 5154 | 2.40 | verified |
| memory_logging | memory_off | 通过 | 不适用 | 4 | 10674 | 8.91 | verified |
| memory_logging | memory_on | 通过 | 不适用 | 0 | 5192 | 4.11 | verified |
| memory_release | memory_off | 通过 | 不适用 | 4 | 10677 | 7.93 | verified |
| memory_release | memory_on | 通过 | 不适用 | 0 | 5157 | 5.34 | verified |
| memory_retries | memory_off | 通过 | 不适用 | 3 | 15799 | 14.61 | verified |
| memory_retries | memory_on | 通过 | 不适用 | 0 | 5150 | 2.83 | verified |
| memory_steps | memory_off | 失败 | 不适用 | 0 | 5227 | 6.10 | verified |
| memory_steps | memory_on | 通过 | 不适用 | 0 | 5137 | 4.58 | verified |
| memory_timeout | memory_off | 通过 | 不适用 | 3 | 15798 | 12.48 | verified |
| memory_timeout | memory_on | 通过 | 不适用 | 0 | 5147 | 4.35 | verified |
| memory_worker | memory_off | 通过 | 不适用 | 3 | 15811 | 11.52 | verified |
| memory_worker | memory_on | 通过 | 不适用 | 0 | 5153 | 2.95 | verified |
| add-endpoint-with-test | all | 通过 | 通过 | 7 | 34420 | 35.12 | verified |
| add-endpoint-with-test | core | 通过 | 通过 | 9 | 38439 | 37.21 | verified |
| config-migration | all | 通过 | 通过 | 19 | 44988 | 44.11 | verified |
| config-migration | core | 通过 | 通过 | 20 | 47833 | 36.16 | verified |
| debug-and-fix | all | 通过 | 通过 | 9 | 33881 | 26.96 | verified |
| debug-and-fix | core | 通过 | 通过 | 10 | 36443 | 27.38 | verified |
| dependency-upgrade | all | 通过 | 通过 | 16 | 43166 | 37.44 | verified |
| dependency-upgrade | core | 通过 | 通过 | 21 | 42911 | 40.45 | verified |
| multi-file-refactor | all | 通过 | 通过 | 28 | 83360 | 61.50 | verified |
| multi-file-refactor | core | 通过 | 通过 | 31 | 62904 | 59.83 | verified |

## 失败与边界

- `memory_steps` / memory_off / attempt 1: answer（verified）

题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。
