# 2026-10-12：成本差距与慢评测的源码诊断

**当前证据支持重构检查点持久化与请求缓存标识，不支持推倒整个 Aster。** 上一轮修正了密文计量，但没有检查完整的本地热路径，也把允许失败题更早结束的全组费用门槛当作扩展依据。共同成功题变慢、变贵时，就应该先定位，而不是立即扩大到二十题。

本次读取保留的二十题轨迹、pi/Codex 历史请求和公开源码，未调用模型、未重跑题目、未改变生产源码或历史分数。新增的[离线分析脚本](analyze.py)可在几秒内重新生成[统计](summary.json)。

## 已确认的差异

| 部分 | 实际证据 | 处理方向 |
|---|---|---|
| 检查点 | Django 单题最终 checkpoint 约 38.3 MB，主要是 `initial_text` 仓库初始文本；每轮多处 checkpoint 重复脱敏、序列化、压缩、落盘 | 初始文本作为不可变基线只保存一次，检查点保存受校验的引用；保留恢复、分支、差异与崩溃边界 |
| 工作区扫描 | `Runtime.checkpoint()` 先 `snapshot()`，紧接着 `fingerprint()` 再调用一次 `snapshot()` | 单个检查点的 hash 从同一份快照生成；不要以不可靠的 mtime 缓存替代安全边界检查 |
| 缓存标识 | Aster **0/349** 请求有 `prompt_cache_key`，pi **291/293**，Codex **268/268**；pi 的两个无键请求也保留 | 为 Responses 请求传递稳定的运行标识，恢复沿用、独立运行/后台任务分开；先做短请求对照验证供应商是否受益 |
| 重复验收 | 78 次 `run_tests` 中，只发现 **1 次**参数与工作区 hash 相同的重复成功检查；有 6 次命令不支持等调用失败 | 继续缩短验收提示不是优先项；先解决可复现的接口误用，不能跳过正式验证来省钱 |
| 评测调度 | 全二十题实际约 **62.6 分钟**，当前固定两题并发；主机 24 核，观测可用内存约 61 GiB | 开发阶段用离线诊断、短缓存探针和固定四题门禁；达标后再执行完整集，并记录提高并发后的条件 |

`Runtime.prepare_request()` 保存检查点，`_model_turn()` 请求前与结算后也保存；`checkpoint()` 调用 `SessionTree.commit()` 后还导出 JSON。当前每份数据包含整个 `initial_text`，因此这些调用不仅保存短小的运行状态。源码位置：[runtime](../../src/aster/core/runtime.py)、[checkpoint](../../src/aster/sessions/checkpoint.py)、[session tree](../../src/aster/sessions/tree.py)、[workspace](../../src/aster/storage/workspace.py)。

二十题中，从 `context_built` 到首次 `model_stream.begin` 合计 **384.597 秒**；从最后一次 `provider_attempt` 到 `model_reply` 合计 **410.134 秒**，共约 **795 秒**。这些间隔包含检查点工作、序列化等，不是纯检查点计时，也不是可以全部消除的时间。工具批次共 **419.031 秒**，另有上下文准备、启动、收尾等未包含在上述间隔中。

用保留的 Django checkpoint 在临时目录调用现有 `SessionTree.commit()` 与 `EvidenceStore.save()`，单次带 cProfile 的诊断耗时约 **1.44 秒**；移出 `initial_text` 与初始权限表后，同一路径约 **0.08 秒**、JSON 约 **1.13 MB**。这是模拟不可变基线分离的微测，不是可恢复格式实现，也不是已经获得的端到端提速。原数据和原始 checkpoint 未改动；[微测记录](checkpoint-profile.json)。

## 从开源实现取什么

本次固定 pi `4270522473e677ddba7b093caa0064a3df51d459`、Codex `a9b0815167c27ab13719dd12d13b63e02b70d221` 阅读。pi 源码包版本为 1.1.0；历史评测使用 pi 1.1.0、Codex CLI 0.154.0。没有将当前 Codex HEAD 的所有行为直接当作历史 CLI 的内部实现；历史请求是否带缓存键由归档本身验证。

- pi 的 [`openai-responses.ts`](https://github.com/earendil-works/pi/blob/4270522473e677ddba7b093caa0064a3df51d459/packages/ai/src/api/openai-responses.ts#L327) 从 session ID 设置 `prompt_cache_key`；Codex 的 [`client.rs`](https://github.com/openai/codex/blob/a9b0815167c27ab13719dd12d13b63e02b70d221/codex-rs/core/src/client.rs#L641) 默认也使用 session ID。可借鉴稳定缓存标识，不能把设置字段等同于保证命中或某个降幅。
- pi 的 [`SessionManager._persist`](https://github.com/earendil-works/pi/blob/4270522473e677ddba7b093caa0064a3df51d459/packages/coding-agent/src/core/session-manager.ts#L1172) 对后续条目追加 JSONL；Codex 的 [`RolloutRecorder`](https://github.com/openai/codex/blob/a9b0815167c27ab13719dd12d13b63e02b70d221/codex-rs/rollout/src/recorder.rs#L1049) 将新增条目交给写入队列。借鉴的是避免反复处理不变的大数据，不需要引入新语言或照搬整套存储系统。
- pi 的 [`estimateContextTokens`](https://github.com/earendil-works/pi/blob/4270522473e677ddba7b093caa0064a3df51d459/packages/coding-agent/src/core/compaction/compaction.ts#L196) 使用最近 usage 加新增消息估算；Codex 的 [`get_total_token_usage`](https://github.com/openai/codex/blob/a9b0815167c27ab13719dd12d13b63e02b70d221/codex-rs/core/src/context_manager/history.rs#L937) 也区分服务端回执与新增内容。这支持此前计量修正的方向，但不代表解决了调用次数与缓存路由问题。

OpenAI 官方缓存与 Codex 配置页面本次仍返回 HTTP 403，未用未读取的页面证明任何行为。源码与版本见[来源清单](sources.json)。

## 建议的实施顺序

1. **先改检查点持久化。** 在现有 EvidenceStore 保存一次带摘要的不可变基线，运行检查点和 SQLite 快照使用引用；旧内联 checkpoint 保持可读，新便携导出须包含基线。一次 checkpoint 复用同一份文件摘要。范围集中在 `sessions/checkpoint.py`、`sessions/tree.py`、`core/runtime.py`、`storage/workspace.py` 及恢复回归；不要删除落盘边界或关闭隔离。检查中断后恢复、工作区漂移、分支隔离、基线丢失/篡改、旧格式导入与便携导出。先以本地大仓库微测证实收益。
2. **单独补齐请求缓存标识。** 让 Runtime 的运行身份经共享请求构造器进入 Responses，保持记录、预算估算与实际发送一致；默认值不改变 Chat/Anthropic。覆盖同运行稳定、恢复稳定、新运行隔离、后台隔离；用固定前缀的短请求探针测量有键/无键，不混入任务通过率。最脆弱的假设是当前兼容供应商真的使用该字段；若不使用，就不能把它作为费用收益来源。
3. **收紧扩展门槛。** 固定 Django 17029（大仓库开销）、Requests 2931（关联调用回归）、Sphinx 11510（长轨迹失败）、Scheme 解释器（终端与绝对路径）四题，每版每题一次，预算仍为 900 秒。之前成功的题不能丢失，费用与耗时先看固定成功题，再看全组；失败题提前退出不能贡献降本准入。通过后才执行完整二十题一次。记录所有请求与缺失用量，pi/Codex 继续冻结。

上述各步可以独立回退和验证，无需同时重写 Runtime、工具、记忆与 UI。新旧基线格式需要明确版本及导出契约，不能以删除恢复能力换取性能；缓存改动不需要新增服务、凭据或用户配置。当前供应商沿用现有连接，不引入第二模型。

## 评测如何更快

先跑无模型的计量重放、恢复回归和持久化微测，淘汰本地就不成立的方案。缓存路由仅做有预算上限的短探针。四题开发门禁每版四并发，解题预算是一批 15 分钟，另加启动、归档与独立评分；需要新旧对照时两版分开计时，不能把它宣传成总共 15 分钟。

全组如果四并发，在保持本轮每题实际耗时不变的离线调度模拟中，解题时间由 **60.7 分钟降至 32.3 分钟**。真实本轮是 62.6 分钟，模拟未包含环境和评分开销，也未模拟供应商限流，不能承诺下一轮必然半小时。四并发需要约 16 核/32 GiB，当前主机资源具备条件；先用小批确认服务延迟与缺失回执不恶化，再将此条件冻结到新轮协议，避免一边运行一边改并发。

复用已经通过且源码/镜像摘要一致的环境预检，解题结束一题即独立评分一题；保留唯一结果与全部失败，不通过删题或降低正式预算缩短最终成绩的运行。更换并发后的新轮与旧历史的运行条件差异必须披露。
