# 第二轮审计补充记录

本记录解释冻结脚本输出的 [REPORT-round2.md](REPORT-round2.md) 和 [audit-round2.json](audit-round2.json)，不改变指标、原始数据或门槛结论。源码为 `v11-stage3` / `8290388c59d21992baf145c8b6237a6848e23969`。

## 执行与结果

依次执行 attempt 1、2、3，每次结束后执行对应 `results`，最后执行 `audit.py --round 2`。全部 34 次尝试完成独立评分：协议规定的 32 次，加上唯一恒过失败题 `pydata__xarray-7233` 的两次条件补测。三组通过数分别为 11/20、5/7、6/7。

- 恒过组首次 9/10；Xarray 两次补测均通过，满足条件补救规则。首次成绩仍保留失败，old20 attempt 1 仍为 11/20。
- 恒败组 0/4；波动组 11/18，达到 ≥ 8/18。
- 两道 aider 题均通过，完整轨迹中接受的 pre-existing 声明为 0。
- `quality_passed=true`，未触发协议的 Stage 3 质量失败回滚条件。
- `all_gates_passed=false`：证据错误 1 项、Requests 带失败轮数 9 > 8、人工核对认定 1 项声明不予豁免。

## COBOL attempt 2 的证据差异

冻结审计将 `2:terminal-cobol-modernization` 记为 `context provenance mismatch`，原因是下列 5 个前台请求未匹配归档上下文。

| 出站 request_id | context_built seq |
|---|---:|
| `bd75a801da0540838c697bfd1165b029` | 1207 |
| `386a6c7bc00e42cdba48521bb82791ba` | 1286 |
| `aecc1f7a785a46bea4e225e4e65611c9` | 1501 |
| `a0fd3b4c03a64e05b1ad356d5925ce72` | 1871 |
| `1bcc2aba145e4f77ac92945204cd9c2c` | 2351 |

逐字段检查发现，这 5 对请求的 `input` 都只在 `input[62].encrypted_content` 不同，类型均为 `reasoning`。出站值长 2680 字符，归档值长 2421 字符并含 `[REDACTED]`。其余输入项、`tools`、`prompt_cache_key` 完全一致。

已确认当前 `src/aster/safety/redaction.py` 与 `v11-stage3` 该文件字节一致；用其中不带任何实际密钥的 `Redactor()` 对出站 `input` 调用 `value(..., mask_fields=False)`，5 次均逐字复现归档 `input`。这说明通用密钥样式匹配误伤了协议中的加密字符串。该任务没有出站前缀重写，也没有 `prefix_drift` 或策略版本异常。

原始证据位于 `artifacts/private/turn-reduction-20261015/round2/attempt2/runs/aster-terminal-cobol-modernization/` 的 `.aster.tar` 和 `provider/`。复核仅离线读取这些证据，未补跑任务或请求模型。由于协议冻结后禁止修改审计定义，本次 `evidence_errors=1` 及门槛失败结论保留。

## pre-existing 人工核对

全部 34 次尝试的完整轨迹共接受 7 项声明，均已逐条写入 [preexisting-review-round2.json](preexisting-review-round2.json)，核对了声明 ID、命令、基线失败结果、工作区哈希和证据 ID。用户已通过三个异步问题完成全部人工核对，结论如下：

| 任务 / 尝试 | 数量 | 人工结论 | 依据 |
|---|---:|---|---|
| Requests-2931 / 1 | 2 | 与任务无关 | 题目处理二进制请求体；声明测试是无请求体 GET 的连接超时测试，环境报网络不可达。 |
| Requests-5414 / 3 | 4 | 与任务无关 | 题目处理非法 URL 标签；声明测试访问有效 IP，环境报网络不可达，超时测试未被修改。 |
| pytest-10356 / 1 | 1 | 不予豁免 | 既有测试验证类标记继承，与本题直接相关；strict-markers 导致收集失败，继承断言未有效执行，不能仅凭基线失败认定无关。 |

用户确认 Requests-2931 的 2 项及 Requests-5414 的 4 项失败与任务无关，已记录 `unrelated=true`；pytest-10356 的 1 项明确不豁免，已记录 `unrelated=false`。7 项均标记 `review_status=human_reviewed`，保留用户原始回复、问题标识和记录时间，无待核对项。

更新记录后重新执行 `audit.py --round 2`，未重新抽样任务。人工核对已经完成，但协议要求所有接受声明均被认定与任务无关，因此 `every_declaration_reviewed_as_unrelated=false`。该失败现已由明确的不豁免结论支持，不再是缺少人工回复。

## 数值解释与限制

attempt 1 的 turns 为 236，第一轮为 304，v10 为 358；前台有用量请求 250，缓存比例约 89.02%，加权成本 2.13270475。加权成本是协议定义的相对估算，不是账单。Xarray-7233、Requests-5414、SymPy-17630、Scheme 解释器在首次尝试因供应商错误终止，不能把较短的轨迹直接归因为 Stage 3 的优化收益。缺失用量未估填。

Sphinx-9230 attempt 1 在 900 秒超时后，已有补丁通过独立评分；Requests-5414 attempts 2、3 的独立评分通过，但 Agent 的结束原因为 `completion_requirements_failed`。这些结果保留在逐次 `results.json` 中，不等同于正常完成交付。

Requests 的 `turns_while_failing` 按协议只取 attempt 1：Requests-2931 为 9，Requests-5414 为 0，合计 9，超过门槛 8。后者因供应商错误提前终止，其 0 值不能解释为成功消除了失败循环。

冻结来源与输入校验通过。生产源码、执行器和指标定义未改动。
