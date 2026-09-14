# Aster 机制评测

固定场景与脚本动作，用于验证机制及消融；不是模型能力总分。
反馈场景使用预先标注的成功/失败，不代表真实模型中的记忆自进化收益。

契约通过：符合该组预期行为。目标达成：开启/关闭两组使用同一目标，允许关闭组未达成。

| 场景 / 组别 | 尝试 | 契约通过 | 目标达成 |
| --- | ---: | ---: | ---: |
| hook_lifecycle/enabled | 3 | 3 | 3 |
| hooks/before_hook_off | 2 | 2 | 0 |
| hooks/before_hook_on | 2 | 2 | 2 |
| memory_admission/evolution_off | 12 | 12 | 12 |
| memory_admission/evolution_on | 12 | 12 | 12 |
| memory_feedback/evolution_off | 6 | 6 | 2 |
| memory_feedback/evolution_on | 6 | 6 | 6 |
| memory_freshness/evolution_off | 4 | 4 | 4 |
| memory_freshness/evolution_on | 4 | 4 | 4 |
| memory_rollback/evolution_off | 2 | 2 | 2 |
| memory_rollback/evolution_on | 2 | 2 | 2 |
| recovery/recovery_off | 2 | 2 | 0 |
| recovery/recovery_on | 2 | 2 | 2 |
| recovery_controls/enabled | 4 | 4 | 4 |
| resume/enabled | 3 | 3 | 3 |
| rl/enabled | 4 | 4 | 4 |
| tool_contract/current | 27 | 27 | 27 |

总计 69 个场景、97 次尝试；契约通过 97/97。
每次执行的完整状态和判定保存在 runs/ 下；失败不从统计中删除。
