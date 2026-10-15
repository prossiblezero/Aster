# Context cache v9：old20 结果

已完成 20/20，已评分 20/20。单次尝试，未择优拼接。

| Agent | 独立通过 | 正常交付且通过 | 请求数 | 总 token | 未缓存输入 | 秒 |
|---|---:|---:|---:|---:|---:|---:|
| Aster v9 | 13/20 | 11/20 | 394 | 10186919 | 2812558 | 8679.5 |
| aster | 15/20 | 12/20 | 405 | 8376153 | 4385656 | 9377.5 |
| pi | 14/20 | 13/20 | 240 | 4936079 | 803852 | 3999.3 |
| codex | 15/20 | 15/20 | 254 | 6212836 | 701823 | 4378.9 |

Aster v9 为本次候选；其余为原 50 题中 old20 的冻结结果。上表含后台记忆用量。

前台缓存指标（排除后台记忆）：

| Metric | aster |
|---|---:|
| tasks | 20 |
| requests | 382 |
| followups | 360 |
| rewrites | 2 |
| rewrite_uncached | 49896 |
| stable_miss | 2098041 |
| stable_hit_mean | 0.783 |
| stable_hit_p10 | 0.228 |
| input | 10043357 |
| cached | 7261184 |
| uncached | 2782173 |
| cache_ratio | 0.723 |
| missing_usage | 2 |
| cache_keys | 382 |


按 PROTOCOL.md 逐项验收，并核对保存的 context_built / model_reply 事件；缓存改善不自动代表通过率或成本目标达标。
