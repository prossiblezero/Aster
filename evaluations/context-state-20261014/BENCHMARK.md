# Context state v10：old20 结果

已完成 20/20，已评分 20/20。单次尝试，未择优拼接。

| Agent | 独立通过 | 正常交付且通过 | 请求数 | 总 token | 未缓存输入 | 秒 |
|---|---:|---:|---:|---:|---:|---:|
| Aster v10 | 13/20 | 12/20 | 410 | 9820687 | 1164777 | 10057.9 |
| Aster v9 | 13/20 | 11/20 | 394 | 10186919 | 2812558 | 8679.5 |
| aster | 15/20 | 12/20 | 405 | 8376153 | 4385656 | 9377.5 |
| pi | 14/20 | 13/20 | 240 | 4936079 | 803852 | 3999.3 |
| codex | 15/20 | 15/20 | 254 | 6212836 | 701823 | 4378.9 |

Aster v10 为本次候选；v9 为上一轮单次结果；其余为原 50 题中 old20 的冻结结果。上表含后台记忆用量。

前台缓存指标（排除后台记忆）：

| Metric | aster |
|---|---:|
| tasks | 20 |
| requests | 398 |
| followups | 358 |
| rewrites | 4 |
| strict_rewrites | 4 |
| low_hits | 25 |
| rewrite_uncached | 80483 |
| stable_miss | 375872 |
| stable_hit_mean | 0.934 |
| stable_hit_p10 | 0.894 |
| input | 9641616 |
| cached | 8536064 |
| uncached | 1105552 |
| cache_ratio | 0.885 |
| missing_usage | 20 |
| cache_keys | 398 |
| mean_input_items | 69.206 |
| mean_input_tokens | 25506.921 |


按 PROTOCOL.md 逐项验收，并核对保存的 context_built / model_reply 事件；缓存改善不自动代表通过率或成本目标达标。
