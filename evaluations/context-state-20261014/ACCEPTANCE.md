# Stage 4 验收

离线读取单次尝试的原始证据；不调用模型、不修改成绩。

| 指标 | 实测 | 门槛 | 达标 |
|---|---:|---:|:---:|
| strict_rewrite_fraction | 0.011173 | <= 0.03 | 是 |
| prefix_drift | 0 | <= 0 | 是 |
| rewrite_without_compaction | 0 | <= 0 | 是 |
| unmatched_foreground_requests | 0 | <= 0 | 是 |
| stable_hit_mean | 0.933698 | >= 0.88 | 是 |
| low_hit_fraction | 0.070621 | <= 0.15 | 是 |
| receipt_estimate_median | 1.002296 | <= 1.02 | 是 |
| receipt_estimate_p95 | 1.023004 | <= 1.05 | 是 |
| cache_ratio | 0.885335 | >= 0.85 | 是 |
| uncached | 1105552 | <= 1300000 | 是 |
| input | 9641616 | <= 10043357 | 是 |
| independent_passes | 13 | >= 15 | 否 |

回执估算 358 次；压缩 4 次；掩码 73 项。

细节、逐题轨迹关联及请求异常见 [audit.json](audit.json)。
