> 历史报告：其中的旧百分比和 token 数字不再作为当前成果依据。请使用 [v2 评测说明](../docs/benchmark.md) 和 v2 报告。

# Aster module benchmark v1

该报告只统计确定性 scripted benchmark，不代表远程模型成功率。

## 总体

- Harness regression：10/10（100%）
- Context cases：12
- Memory retrieval cases：12
- Recovery cases：10
- Completion cases：9

## Context

- 平均 token：708.9 → 581.9
- 平均压缩率：11.23%
- 最高压缩率：59.09%
- 预算内率：100.00%
- 当前请求保留率：100.00%
- 重要验证证据保留率：100.00%

## Memory

- Recall@3：100.00%
- MRR：1.000
- Top-3 平均 precision：100.00%
- 含无关项的 Top-3 比例：0.00%
- follow-up 重复读取：12 → 0（减少 100%）
- Evolution：{"support_count": 2, "contradiction_count": 1, "provenance_rejection": true, "snapshot_rollback": true, "successful_use_feedback": true, "dedup_after_dream": true, "recorded_support": true}

## Recovery and completion

- checkpoint resume 成功率：100.00%
- workspace drift 拒绝率：100.00%
- runtime identity mismatch 拒绝率：100.00%
- stall pattern 检出率：100.00%
- 有界恢复耗尽后停止：True
- completion contract enforcement rate：100.00%

## Working memory and hooks

- working memory summary recorded：True
- next prompt contains the observed fact：True
- edit invalidates stale fact：True
- hook plugin chain and exception isolation：True / True

## Runtime features

- max_turns boundary：True
- auto_dream enabled event：True
- auto_dream disabled event absent：True
- tool output token clipping：True
- core tools / all tools：9 / 26
- core profile hides unsupported worker_message：True

## 解释边界

这些结果证明 Aster 的机制在固定输入和物理证据下满足契约。Memory follow-up 是 scripted policy
对照，不是 GPT 成功率；召回实验使用固定关键词 query，不代表开放域语义检索。当前实现也没有
文件新鲜度驱动的陈旧记忆拒绝。真实模型仍需单独运行、多次重复，并报告模型、温度、任务集和失败样本。
