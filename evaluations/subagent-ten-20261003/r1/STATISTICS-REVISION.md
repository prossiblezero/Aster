# 父输入完整性补充

r1 全部 20 回合结束后增加父输入独立完整性字段：存在 total_tokens 并不证明 input_tokens 已报告。新回合检查每个父请求及 HTTP 尝试的 input_tokens；旧结果保守要求全部请求均有 input_tokens_requests 计数，才将父输入比率纳入比较。

r0/r1 的父请求都实际包含 input_tokens，本修正不改变它们的分数或已公布配对比率。单独构造的 total-only 回归例在修正前产生错误的 0.0 父输入比率，修正后为 null。旧 r1 驱动及 17 份哈希依赖保存在私有 original-harness，原统计保存在 original-*.json，原 FREEZE 和原 episode results 不变。
