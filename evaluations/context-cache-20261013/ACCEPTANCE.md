# Stage 4 验收

离线读取单次尝试的原始证据；不调用模型、不修改成绩。

| 指标 | 实测 | 门槛 | 达标 |
|---|---:|---:|:---:|
| rewrite_fraction | 0.005556 | <= 0.03 | 是 |
| rewrite_uncached | 49896 | <= 250000 | 是 |
| prefix_drift | 0 | <= 0 | 是 |
| rewrite_without_compaction | 0 | <= 0 | 是 |
| unmatched_foreground_requests | 0 | <= 0 | 是 |
| stable_hit_mean | 0.783453 | >= 0.85 | 否 |
| receipt_estimate_median | 1.003858 | <= 1.02 | 是 |
| receipt_estimate_p95 | 1.024513 | <= 1.05 | 是 |
| cache_ratio | 0.722984 | >= 0.78 | 否 |
| uncached | 2782173 | <= 1300000 | 否 |
| input | 10043357 | <= 9450000 | 否 |
| independent_passes | 13 | >= 15 | 否 |

回执估算 359 次；压缩 3 次；掩码 48 项。

细节、逐题轨迹关联及请求异常见 [audit.json](audit.json)。
