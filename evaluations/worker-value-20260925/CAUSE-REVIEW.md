# 人工因果解释复核（2026-09-25）

范围：分阶段只读核对开发试跑与最终确认的 `report.json`，检查 `cause` 与实际源码、合同、输入样例是否一致，并核查委派子任务的成功读源轨迹与交付文本。最终覆盖 6 份开发报告及 8 份确认报告；不修改任务、报告、grader 或产品。结构化结果仍以独立 grader 为准，人工复核不覆盖或放宽机器验收。开发与确认分开统计。

| 批次 / 运行 | accounts 因果解释 | objects 因果解释 | search 因果解释 | 报告整体 |
|---|---|---|---|---|
| baseline / retention-audit-solo-r1 | 正确：按开户时间计算冷静期，Alice 提前删除 | 正确：仅选择 current，漏删两个可删除旧版本 | 正确：只扫 public，漏掉仍提供服务的 staff 索引 | 机器 4/4，通过；三个 cause 均正确 |
| baseline / retention-audit-workers-r1 | **部分错误**：根因识别正确，但声称 Alice **和 Eve** 均提前删除，与代码执行和样例不符 | 正确 | 正确 | **失败**：机器 3/4；不能因大致找对根因改判通过 |
| v2-dev / retention-audit-workers-r1 | 正确：仅 Alice 因错误时间起点提前删除 | 正确 | 正确 | 机器 4/4，通过；三个 cause 均正确 |
| v3-dev / retention-audit-workers-r1 | 正确：仅 Alice 因错误时间起点提前删除 | 正确 | 正确 | 机器 4/4，通过；三个 cause 均正确 |

核对依据：

- `services/accounts.py` 的 `eligible_for_delete` 用 `user['created_at']`，合同要求用 `request['requested_at']`。批次时间为 9 月 25 日 12:00 UTC，冷静期 72 小时；Alice 请求仅 24 小时、开户已久，因此实际提前删除。Eve 开户与请求均为 9 月 23 日 12:00 UTC，只有 48 小时；即使使用错误起点也仍应保留。baseline workers 的 `actual.deleted_user_ids` 与 `affected_ids` 都错误包含 Eve，其 cause 同样重复该错误。Dan 恰好到达 72 小时，应该删除；Carol 因 legal hold 保留，但会话仍撤销。
- `services/objects.py` 的 `candidates` 只产出 `is_current=True`；合同覆盖所有物理版本。`report-v1`、`report-v2` 的最小保留期均已过且无 hold/retain-until 限制，理应与 `report-v3` 一起删除。四份 cause 都准确描述这个选择缺口。
- `services/search.py` 的 `deletion_candidates` 仅采用 `serving_aliases['public']`，而当前流量也经过指向 `staff_v2` 的 staff/moderation 别名；`staff-bob`、`staff-eve` 因此漏删。退休索引不算 serving，其他租户与作者不受影响。四份 cause 都正确。

可报告口径：四份报告的 12 个 cause 中，11 个完整正确；1 个根因大致正确但具体受影响对象错误。该错误所在完整任务必须保留为失败。机器验收检查事实、影响集合和路径引用；自由文本语义由本次人工复核补充。

原始目录：
- `/tmp/aster-worker-value-baseline-run-20260925`
- `/tmp/aster-worker-value-v2-dev-20260925`
- `/tmp/aster-worker-value-v3-dev-20260925`

以下继续追加 V4/V5 开发记录与最终候选 8 次确认记录。

## 开发阶段追加：V4 / V5

| 批次 | accounts cause | objects cause | search cause | 机器验收 |
|---|---|---|---|---|
| v4-dev / retention-audit-workers-r1 | 正确，明确 Alice 请求仅 24h | 正确，漏掉非 current 物理版本 | 正确，仅 public alias 扫描漏掉 staff_v2 | 4/4，通过 |
| v5-dev / retention-audit-workers-r1 | 正确，仅 Alice 提前删除 | 正确，report-v1/v2 被错误保留 | 正确，未覆盖全部 serving_aliases | 4/4，通过 |

两版新增 6 个 cause 全部正确。开发阶段累计 6 份报告、18 个 cause，其中 17 个完整正确，baseline workers/accounts 仍为错误项；开发试跑不能并入最终确认样本。

子任务读取轨迹复核：

| 批次 / 子任务 | 子运行 ID | 完整读取的核心证据 | 成功执行公共接口 | 结论支持 |
|---|---|---|---|---|
| V4 accounts | e216817cbfd5481b8fb5e019241dd3e5 | contracts/accounts.md、fixtures/accounts.json、services/accounts.py | 是，python -c 调用 plan | 支持 |
| V4 objects | ae85e4d4bf944ab4913b6ef846c010d7 | contracts/objects.md、fixtures/objects.json、services/objects.py | 是，python -c 调用 plan | 支持 |
| V4 search | 7918c613532a44cf87b24a42b25b5475 | contracts/search.md、fixtures/search.json、services/search.py | 否，heredoc 被 approval_unavailable 阻止 | 源码推导支持；子任务明确披露执行受阻 |
| V5 accounts | 321f9f0c41b644318eac95e6d4a9c715 | contracts/accounts.md、fixtures/accounts.json、services/accounts.py | 是，python -c 调用 plan | 支持 |
| V5 objects | 6626e78c5f0742a1901007e699bf11f4 | contracts/objects.md、fixtures/objects.json、services/objects.py | 否，heredoc 被 approval_unavailable 阻止 | 源码推导支持，未声称执行成功 |
| V5 search | 2c5aab2d64144b24b343644451908797 | contracts/search.md、fixtures/search.json、services/search.py | 否，heredoc 被 approval_unavailable 阻止 | 源码推导支持，未声称执行成功 |

六个子任务均通过成功 read_file 读取各自三个核心文件的完整行范围，trace 中 source_sha256 与磁盘文件一致，并额外读取 README 和 smoke tests。报告 evidence 中的核心路径不是凭空引用。V5 search 子任务只交付受影响文档的前后变化，未完整列出正常 public 文档；父任务随后确实重新读取 search 的三个文件，最终补齐的列表正确。完整文件读取与正确报告不能证明所有推理过程都被执行验证；stop_reason=verified 也不能解读成测试通过。V5 父任务最终验证命令出现语法错误及审批阻止，最终回复如实披露阻止；独立 grader 仍是完整交付正确性的依据。

原始追加目录：`/tmp/aster-worker-value-v4-dev-20260925`、`/tmp/aster-worker-value-v5-dev-20260925`。

## 最终确认阶段（8 项完成，人工复核完成于 2026-09-26）

### reconciliation-audit-solo-r1 的失败确认

这是模型结论错误，不是评分歧义。该运行的 bank/subscriptions 因果解释、结构化实际和期望结果均正确；marketplace 的 cause 也正确指出“未转换 business timezone”，但错误推导了期望选中集合和净额，并漏报一个受影响边界记录。

合同明确要求先转 `business_timezone` 再取日历日期，批次指定 `Europe/Berlin` 与 `2026-09-25`。用标准库 `ZoneInfo` 独立换算得到：

| payout | UTC settled_at | 柏林当地时间 | 应计入 9 月 25 日 | 净额 cents |
|---|---|---|---|---|
| p-early | 2026-09-24 22:30 | 2026-09-25 00:30 +02:00 | 是 | 9000 |
| p-noon | 2026-09-25 12:00 | 2026-09-25 14:00 +02:00 | 是 | 4500 |
| p-late | 2026-09-25 22:30 | 2026-09-26 00:30 +02:00 | **否** | 1800（不计入） |
| p-refund | 2026-09-25 14:00 | 2026-09-25 16:00 +02:00 | 是 | -900 |

正确期望选中 `p-early,p-noon,p-refund`，净额 `9000+4500-900=12600`；影响集合是 `p-early,p-late`。模型报告却额外选入 `p-late`，给出 `14400` 且 affected_ids 只有 `p-early`。当前实现直接取 provider 日期，实际 `5400` 的报告是对的。时区、输入偏移、结算日、当地午夜规则均在公开文件中明确；不需要猜测任何隐藏规则。因此即使该条 cause 方向正确，完整任务仍必须判失败。

### 8 份最终报告逐项复核

下表“子系统结果”包含实际输出、合同期望、影响集合与证据路径的机器验收，并经人工检查原因；不把正确的根因概述等同于完整答案正确。

| 最终运行 | 三个 cause 的根因表述 | 子系统结果 | 完整任务 |
|---|---|---|---|
| retention-audit-solo-r1 | accounts / objects / search 均正确 | 3/3 正确 | 通过 |
| retention-audit-workers-r1 | accounts / objects / search 均正确 | 3/3 正确 | 通过 |
| reconciliation-audit-solo-r1 | bank / subscriptions / marketplace 的机制根因均正确，但 marketplace 后果推导错误，详见上文 | **2/3 正确** | **失败** |
| reconciliation-audit-workers-r1 | bank / subscriptions / marketplace 均正确 | 3/3 正确 | 通过 |
| retention-audit-workers-r2 | 机制根因均正确；accounts 的 “request to be deleted” 措辞不够准确，应是 account 被列入删除计划，结构化字段明确且正确 | 3/3 正确 | 通过 |
| retention-audit-solo-r2 | accounts / objects / search 均正确 | 3/3 正确 | 通过 |
| reconciliation-audit-workers-r2 | bank / subscriptions / marketplace 均正确，边界时区换算与净额准确 | 3/3 正确 | 通过 |
| reconciliation-audit-solo-r2 | bank / subscriptions / marketplace 均正确；marketplace 正确排除 p-late、纳入 p-early，净额 12600 | 3/3 正确 | 通过 |

最终报告的正确性为 **23/24 个子系统，7/8 个完整任务**。其中 solo 为 11/12 个子系统、3/4 个任务；workers 为 **12/12 个最终子系统、4/4 个任务**。不能据此声称所有 child 交付毫无错误，也不能以这两题各两次的结果推断总体通过率得到稳定提升。

### 最终 workers 的子任务证据与执行覆盖

12 个子任务均成功完整读取对应 implementation、contract、fixture 三个核心文件，共 36 次核心文件覆盖；每次 trace 的 `source_sha256` 均与原始文件一致。此项只证明证据可达且确实被读取，不能单独证明其全部解释正确。八个子任务成功执行对应 fixture 的公共接口，四个因 heredoc 审批限制未成功执行该接口；其中 reconciliation r1 的 marketplace 另成功运行公开 smoke tests（3 passed）。公开 smoke 不覆盖全部缺陷，不能替代独立 grader。

| 最终运行 / 子系统 | 子运行 ID | 核心文件完整读取 / 哈希核对 | 对应 fixture 公共接口执行 |
|---|---|---|---|
| retention workers r1 / accounts | bde51f0bfa664f69b086c2b8c7f3937e | 3/3 | 成功 |
| retention workers r1 / objects | b838533bc7c54d778a96c39e586f4baa | 3/3 | 成功 |
| retention workers r1 / search | 003b5772d629433095b6757cd368663b | 3/3 | 成功 |
| reconciliation workers r1 / bank | 9e6615c78d7e4ec48a378d3a61c6c67f | 3/3 | 审批阻止 |
| reconciliation workers r1 / subscriptions | b758c43507ca492a840258b33b0d2ee9 | 3/3 | 成功 |
| reconciliation workers r1 / marketplace | c61306673aa94d5e9e807243f45c2677 | 3/3 | 审批阻止；smoke 3 passed |
| retention workers r2 / accounts | 12ea0bd477c544269b5ac754fb45dd53 | 3/3 | 成功 |
| retention workers r2 / objects | c91437617b064e1dbd2c187c633ef7b5 | 3/3 | 审批阻止 |
| retention workers r2 / search | f36f9d8d2e284ec19cc7f74e50848258 | 3/3 | 成功 |
| reconciliation workers r2 / bank | 9c2d643d12104a64b177e766a6c886f2 | 3/3 | 成功 |
| reconciliation workers r2 / subscriptions | 2bdc71022b6f45c097ec57ed86f891b2 | 3/3 | 审批阻止 |
| reconciliation workers r2 / marketplace | be26dc48f76c4646944b808456cad61f | 3/3 | 成功 |

### 子任务交付中的两项额外事实错误

- `retention-audit-workers-r2` 的 accounts 子任务（12ea…）正确给出所有实际/期望列表及受影响对象 Alice，但额外说明把 Eve 的请求年龄写成 **24 小时**。fixture 中 Eve 的 `requested_at` 与 `created_at` 均为 9 月 23 日 12:00 UTC，相对批次时间均是 **48 小时**。最终父报告没有携带这项错误年龄，保留 Eve 的结论正确。已经成功执行实际 `plan()` 也不意味着合同解释的每个附加数字都正确。
- `reconciliation-audit-workers-r1` 的 subscriptions 子任务（b758…）正确算出余额和事件分类，但其额外 `affected_event_ids` 写成 `[evt-r1-replay, evt-r1, alex]`。按任务的输入事件 ID 定义，只应包含重复退款信封 `evt-r1-replay`；原始 `evt-r1` 正常接收，`alex` 是客户 ID 而非事件 ID。最终父报告已正确收敛为仅 `evt-r1-replay`，因此最终报告通过不等于该 child 的全部交付字段都正确。

其余已审阅的 child 最终交付未发现新的实质事实错误。上述两个问题必须作为人工复核局限保留；不对内部额外文本另行赋予“完全正确率”，也不把父报告通过率包装成 child 可靠性指标。

确认数据目录：`/tmp/aster-worker-value-confirmation-20260925`。本次人工复核只读取既有结果、源码、合同、fixture、checkpoint 与 trace；没有调用模型、重试任务或修改候选。
