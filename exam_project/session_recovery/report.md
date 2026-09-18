# 会话恢复与具名配置切换实战报告

日期：2026-09-18。**阶段 1 的模拟故障恢复完成并通过独立验收；阶段 2 被真实消息历史兼容问题阻断，未调用模型。** 一次真实尝试，无候选人工修改、无失败重跑。修复后的离线回归不能替代阶段 2 的真实验收。

## 冻结条件与责任

- 产品源码提交：`d20c38fface3751fd366e303e77c81d6ce66c6f8`；Python 源码摘要：`23345dc01ea8e13fd902cda9799befcdcc711f3b08b63fc22edff62a6ad2149f`。协议、驱动与验收摘要见 [experiment.json](experiment.json)，完整协议见 [README.md](README.md)。
- 使用 `/home/dzh/.conda/envs/aster/bin/python`，进程启动前指定冻结源码的 PYTHONPATH，并校验实际导入路径。没有安装依赖。
- 固定 `gpt-5.6-sol`、Responses、非流式、128,000 总上下文、32,768 输出、300 秒请求超时；Memory/Dream/Skills/Worker 关闭，只读工具并行度 1；required 沙箱、禁网。冻结版本没有环境复用功能。
- 阶段 1 从空目录实现 `uniqwords`，故障前后共同限 30 步、16 轮、1,800 秒。第一次成功写入之后的下一次 `complete()` 在 HTTP 之前注入且仅注入一次 `ProviderError`；模拟故障也消耗一轮。
- 新建 Runtime 后实际执行 `resume()` 与 `continue_task("继续剩余工作。")`。阶段 2 预定在恢复后的同一 Runtime 调用 `switch_model("compact-sol")`，模型、协议、端点、凭证和权限不变，仅总上下文改为 64,000，再自然跟进增加 `--fold`。阶段 2 另有 30 步、16 轮、1,800 秒，但本轮未到达模型调用。
- Aster 独立创建代码、README、示例和 11 项自测，并自行修改、运行测试。人工提供冻结需求及一次通用恢复信号，未提供候选错误提示、未修补候选。预定的 `--fold` 跟进因切换失败尚未送给模型。

## 实测结果

| 阶段 | 工具步数 | Runtime 轮数 | 真实 HTTP 请求 | 耗时 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| 故障前 | 6 | 3 | 2 | 56.96 秒 | 五次成功文件写入后，下一次 complete 注入故障 |
| 新 Runtime 恢复后 | 18 | 6 | 6 | 68.69 秒 | completed；最终自测 11/11，独立验收 8/8 |
| 阶段 1 合计 | 24 | 9 | 8 | 125.69 秒 | 共享预算满足，含恢复设置开销 |
| 阶段 2 | 0 | 0 | 0 | — | profile 切换被历史校验拒绝，未开始跟进 |

恢复时实际剩余额度为 **24 步、13 轮、1,743.00 秒**，没有重置到完整预算。独立验收先验证空目录负控制：8 项全部执行，2 项错误处理检查通过、6 项失败，整体拒绝；阶段 1 完成后 8/8 通过。阶段 2 的预功能负控制执行 12 项，仅原有 8 项通过，新增 4 项 `--fold` 检查失败。负控制和独立测试正文未提供给 Aster。

保存的 [run/result.json](run/result.json) 仍为 `passed: false`、`driver_error: ContextHistoryError`、`driver_message: unsupported_history_fields`；`stages_attempted` 为 1，阶段 2 保持 `not_started`。候选代码仍只实现阶段 1，见 [uniqwords.py](run/candidate/uniqwords.py)。

## 恢复与来源审计

[run/audit.json](run/audit.json) 记录了运行后只读检查：

- 两个 run 属于同一 session。恢复时文件摘要、已用工具 ID、原生消息历史与故障前完全相同。
- 首次写入 ID 只执行一次；全部 24 个执行过的工具 ID 唯一，未发现重放。恢复后新的修改使用新 ID，文件版本检查正常。
- 原 run 有 6 对原生工具调用/结果；续聊 checkpoint 含完整 24 对。调用与结果配对完整，两个 trace 的事件摘要链有效。
- 全部 24 次工具执行成功；两次 Aster 自测均为 11/11、`landlock+seccomp` 隔离。独立验收也在独立 required 沙箱中执行。
- 模拟故障明确标记 `before_http: true`、HTTP 尝试 0；实际 8 次请求、8 次 HTTP 尝试、0 次 HTTP 重试、0 次真实请求失败。不能把该注入计为服务端错误。
- 冻结源码和实验文件摘要未变；最终候选摘要与保存结果一致。运行结果及实验目录的凭证扫描均未发现明文凭证。原始请求、trace、checkpoint 和独立验收输出本地保留在已忽略目录中。

运行消耗为输入 **87,041**、输出 **4,420**、总量 **91,461** tokens；其中缓存读取 **21,504**、reasoning **1,242**。这些字段 8/8 请求均报告。缓存写入与未缓存输入未报告，保留 `null`，不填零、不作扣减估算。本实验没有缓存开关对照，不据此归因缓存收益。

## 阻断定位与后续离线修复

真实 Responses 原生 assistant message 带有 `phase`：本轮 7 条 `commentary`、1 条 `final_answer`。冻结版本的 `history.canonicalize()` 未将 `phase` 纳入消息字段白名单，因此实际 `switch_model("compact-sol")` 在转换预检查中抛出 `unsupported_history_fields`。这发生在修改 Runtime 配置之前，原会话仍可保留；没有向较小窗口发请求，也没有执行阶段 2。

在原始 checkpoint 上只读复现可以得到同一错误。仅在内存诊断副本移除 `phase` 后转换成功，从而定位字段；该副本没有写回任何证据、候选或源码。**最终修复不是丢弃 phase**：[官方 OpenAI reasoning 文档](https://developers.openai.com/api/docs/guides/reasoning#phase-parameter) 明确要求手动回放时保留 assistant 的原始 phase，以免把进度说明当作最终答复。2026-09-18 实际检索并读取了该页面，其 round-trip 示例采用 `role: assistant`、字符串 `content` 和 `phase`，没有要求原来的 `type/id/status`。

后续修复 `8f31a0e` 校验并保留 `commentary` / `final_answer`，Responses 切换和回放继续携带它们；可选 `null` 表示未提供 phase。非法类型、未知值、非 assistant 的 phase 均拒绝。当前 Chat/Anthropic 转换没有等价语义表示，明确拒绝有效 phase 的跨协议切换，避免静默丢失语义；这是仍存在的限制。

本轮修复验证：

- 离线实验框架 **8 项通过**，没有真实模型调用。
- `test_history_phase.py`、`test_history_conversion.py`、`test_session_continuation.py`、`test_protocol_pairing.py`、`test_streaming.py` 共 **155 项通过**；Ruff 与差异空白检查通过。
- 独立复核确认同一 155 项通过，并额外验证带 phase 的 checkpoint 恢复到 Chat/Anthropic 失败时，已有会话的 state/settings/history/redactor 和 checkpoint 字节不变。
- 修复后的转换器只读回放本轮真实历史，得到 57 条可移植消息，8 条 phase 原值均保留，8 条原生 reasoning 计为仅保存在原始证据中；该检查未执行 Runtime、未调用 HTTP，结果单列在 audit 中。

下一次若继续验证阶段 2，需要新的明确实验记录与预算，不能覆盖本目录、重复本次尝试或把这次失败改记成功。带 phase 的跨协议语义映射也尚未实现。
