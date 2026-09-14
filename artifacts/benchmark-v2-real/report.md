# Aster benchmark v2

模式：real；任务内容哈希：`740e28fe307b9957369bf2fc3296d16aaa72211e8e6c38d40d6043c16b34388c`。

脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。

| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 全部尝试平均 token |
|---|---:|---:|---:|---:|---:|
| function/all | 12 | 12 | 12/12 | 5.58 | 11129 |
| function/core | 12 | 12 | 12/12 | 6.50 | 7572 |
| memory/memory_off | 12 | 12 | 11/12 | 2.92 | 3592 |
| memory/memory_on | 12 | 12 | 11/12 | 0.00 | 879 |
| repository/all | 5 | 5 | 2/5 | 11.20 | 10376 |
| repository/core | 5 | 5 | 2/5 | 12.00 | 9146 |

记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。

## 逐题结果

core/all 分别为核心工具集/全部工具；memory_off/on 为关闭/开启记忆。两个条件不合并为一次重试。

| 任务 | 条件 | 完成 | 外部代码测试 | 工具调用 | 总 token | 秒 | 结束原因 |
|---|---|---|---|---:|---:|---:|---|
| humaneval_0 | all | 通过 | 通过 | 5 | 9141 | 118.13 | verified |
| humaneval_0 | core | 通过 | 通过 | 7 | 5560 | 107.10 | verified |
| humaneval_1 | all | 通过 | 通过 | 5 | 12305 | 95.68 | verified |
| humaneval_1 | core | 通过 | 通过 | 7 | 7047 | 89.23 | verified |
| humaneval_10 | all | 通过 | 通过 | 9 | 11052 | 107.95 | verified |
| humaneval_10 | core | 通过 | 通过 | 7 | 7222 | 95.22 | verified |
| humaneval_11 | all | 通过 | 通过 | 7 | 14049 | 89.28 | verified |
| humaneval_11 | core | 通过 | 通过 | 5 | 9420 | 66.81 | verified |
| humaneval_2 | all | 通过 | 通过 | 5 | 13373 | 54.97 | verified |
| humaneval_2 | core | 通过 | 通过 | 8 | 6678 | 56.38 | verified |
| humaneval_3 | all | 通过 | 通过 | 5 | 13217 | 41.46 | verified |
| humaneval_3 | core | 通过 | 通过 | 7 | 5405 | 18.83 | verified |
| humaneval_4 | all | 通过 | 通过 | 5 | 13564 | 80.78 | verified |
| humaneval_4 | core | 通过 | 通过 | 5 | 6316 | 63.75 | verified |
| humaneval_5 | all | 通过 | 通过 | 4 | 7254 | 38.36 | verified |
| humaneval_5 | core | 通过 | 通过 | 5 | 6158 | 42.02 | verified |
| humaneval_6 | all | 通过 | 通过 | 5 | 9278 | 118.12 | verified |
| humaneval_6 | core | 通过 | 通过 | 8 | 11273 | 74.51 | verified |
| humaneval_7 | all | 通过 | 通过 | 5 | 7291 | 35.33 | verified |
| humaneval_7 | core | 通过 | 通过 | 8 | 11095 | 74.62 | verified |
| humaneval_8 | all | 通过 | 通过 | 7 | 13982 | 60.13 | verified |
| humaneval_8 | core | 通过 | 通过 | 5 | 4880 | 60.90 | verified |
| humaneval_9 | all | 通过 | 通过 | 5 | 9044 | 87.78 | verified |
| humaneval_9 | core | 通过 | 通过 | 6 | 9815 | 67.73 | verified |
| memory_api | memory_off | 通过 | 不适用 | 3 | 6938 | 35.52 | verified |
| memory_api | memory_on | 通过 | 不适用 | 0 | 960 | 13.55 | verified |
| memory_cache | memory_off | 通过 | 不适用 | 4 | 3192 | 64.16 | verified |
| memory_cache | memory_on | 通过 | 不适用 | 0 | 968 | 23.82 | verified |
| memory_database | memory_off | 通过 | 不适用 | 4 | 2941 | 62.31 | verified |
| memory_database | memory_on | 通过 | 不适用 | 0 | 966 | 14.03 | verified |
| memory_deploy | memory_off | 通过 | 不适用 | 3 | 6933 | 62.66 | verified |
| memory_deploy | memory_on | 通过 | 不适用 | 0 | 951 | 7.59 | verified |
| memory_health | memory_off | 通过 | 不适用 | 2 | 1897 | 38.18 | verified |
| memory_health | memory_on | 通过 | 不适用 | 0 | 955 | 21.98 | verified |
| memory_image | memory_off | 通过 | 不适用 | 3 | 1977 | 50.91 | verified |
| memory_image | memory_on | 通过 | 不适用 | 0 | 953 | 18.60 | verified |
| memory_logging | memory_off | 通过 | 不适用 | 4 | 2112 | 36.94 | verified |
| memory_logging | memory_on | 通过 | 不适用 | 0 | 974 | 13.88 | verified |
| memory_release | memory_off | 通过 | 不适用 | 4 | 2179 | 10.02 | verified |
| memory_release | memory_on | 通过 | 不适用 | 0 | 954 | 16.59 | verified |
| memory_retries | memory_off | 通过 | 不适用 | 2 | 5973 | 38.67 | verified |
| memory_retries | memory_on | 通过 | 不适用 | 0 | 953 | 14.71 | verified |
| memory_steps | memory_off | 失败 | 不适用 | 0 | 0 | 90.18 | provider_error |
| memory_steps | memory_on | 通过 | 不适用 | 0 | 949 | 15.15 | verified |
| memory_timeout | memory_off | 通过 | 不适用 | 2 | 2752 | 63.33 | verified |
| memory_timeout | memory_on | 通过 | 不适用 | 0 | 960 | 36.02 | verified |
| memory_worker | memory_off | 通过 | 不适用 | 4 | 6206 | 20.45 | verified |
| memory_worker | memory_on | 失败 | 不适用 | 0 | 0 | 0.20 | provider_error |
| add-endpoint-with-test | all | 通过 | 通过 | 8 | 16490 | 111.73 | verified |
| add-endpoint-with-test | core | 通过 | 通过 | 8 | 9160 | 121.85 | verified |
| config-migration | all | 失败 | 通过 | 10 | 5671 | 37.50 | stall_recovery_exhausted |
| config-migration | core | 失败 | 通过 | 10 | 3771 | 30.35 | stall_recovery_exhausted |
| debug-and-fix | all | 通过 | 通过 | 8 | 10150 | 100.04 | verified |
| debug-and-fix | core | 通过 | 通过 | 9 | 11781 | 87.62 | verified |
| dependency-upgrade | all | 失败 | 通过 | 10 | 5694 | 47.65 | stall_recovery_exhausted |
| dependency-upgrade | core | 失败 | 通过 | 12 | 8521 | 42.68 | stall_recovery_exhausted |
| multi-file-refactor | all | 失败 | 失败 | 20 | 13873 | 55.98 | provider_error |
| multi-file-refactor | core | 失败 | 通过 | 21 | 12495 | 89.75 | stall_recovery_exhausted |

评分修正：端点题未指定 JSON 内容，删除验证器额外添加的 status=ok 条件后，对两个条件的原始产物重新评分；未重新调用模型。原判定与修正证据见 grader-corrections.json，原始尝试记录保持不变。

## 失败与边界

- `memory_steps` / memory_off / attempt 1: phase, answer（provider_error）
- `memory_worker` / memory_on / attempt 1: phase, answer（provider_error）
- `config-migration` / all / attempt 1: phase（stall_recovery_exhausted）
- `config-migration` / core / attempt 1: phase（stall_recovery_exhausted）
- `dependency-upgrade` / all / attempt 1: phase（stall_recovery_exhausted）
- `dependency-upgrade` / core / attempt 1: phase（stall_recovery_exhausted）
- `multi-file-refactor` / all / attempt 1: phase, independent_tests（provider_error）
- `multi-file-refactor` / core / attempt 1: phase（stall_recovery_exhausted）

题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。

记忆共同成功任务：10 条；其后续工具调用 31 → 0，token 36894 → 9594。此子集成本补充全量完成率，不替代失败记录。

接口失败分类：memory_steps / memory_off：ReadTimeout。
接口失败分类：memory_worker / memory_on：ConnectError。
接口失败分类：multi-file-refactor / all：HTTP 400。
