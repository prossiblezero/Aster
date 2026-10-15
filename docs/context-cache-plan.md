# 上下文缓存优化实施方案（2026-10-13）

本文档写给实施代码的 Agent（Codex）。目标是把 Aster 的未缓存输入降到 pi、Codex 的同一量级，同时不降低通过率。

实施与评测记录（2026-10-14）：Stage 0–4 已完成。old20 单次评测为 **13/20**（基线 15/20），含后台的总 token 增加 **21.6%**、未缓存输入减少 **35.9%**；历史改写与回执计量达标，但缓存、总输入和质量门槛未通过。完整成绩与证据见 [实施及评测报告](../evaluations/context-cache-20261013/REPORT.md)。

## 0. 使用规则

1. 按 Stage 0 → 4 的顺序实施。每个 Stage 先通过它自己的“验收”，再进入下一个。
2. 第 10 节“不要改”优先于其他任何内容。旧文档、旧补丁与本文冲突时，以本文为准。
3. 行号基于提交 `0ce78db`。定位以**函数名**为准，行号只作参考。
4. 动手前先跑一遍全量测试，保存失败清单作为基线（已知存在 4096 token 紧预算下的既有失败）。之后只报告相对基线的变化，不要声称修好了基线里的失败。
5. Stage 4 会调用模型、消耗额度。准备好协议和脚本后**停下，等用户确认**再运行。
6. 不新增依赖、配置项、模型调用或服务。

测试命令（在装有 `tiktoken` 的项目环境中运行）：

```bash
python -m ruff check src tests
python -m pytest -q -m core
python -m pytest -q -m "not core"
```

## 1. 问题与证据

数据来自 2026-10-12 的 50 题同模型评测（Aster `c6b651d`）。逐请求归档在 `artifacts/private/general-coding-50-20261012/runs/<agent>-<task>/provider/`。

| 指标（50 题） | Aster | pi | Codex |
| --- | ---: | ---: | ---: |
| 独立通过 | 36 | 37 | 36 |
| 输入 token 合计 | 16,486,579 | 12,474,890 | 12,018,776 |
| 未缓存输入 | **8,576,179** | 1,808,650 | 1,534,552 |
| 缓存占比 | **48.0%** | 85.5% | 87.2% |
| 改写了已发送历史的请求 | **259 / 761** | 10 / 553 | 0 / 481 |
| 改写请求的未缓存输入 | **5,408,244** | 232,582 | 0 |
| 前缀未变却未命中的 token | 2,134,725 | 761,086 | 734,182 |

三个根因：

1. **裁剪每轮改写历史，并写回持久化状态。** `BudgetCalculator.calculate` 的 `while tokens(prompt) > limit` 循环每次只裁到刚好放下；`ContextBuilder.build` 再把结果写回 `structured_history`。下一轮追加内容后又超限、再裁一次。提示词缓存只认完全相同的前缀，被改动位置之后的内容全部重新计费。
2. **本地 token 估算约为实际的 1.78 倍。** `BudgetCalculator.count` 用 `cl100k_base` 对整个 JSON 请求计数，把推理项的 `encrypted_content` 当普通文本。实测这些密文在服务端约 25 字符/token，本地约 1.5 字符/token。结果是实际输入只有约 2.5 万 token 时就开始裁剪（预算 55,808）。
3. **不发送 `prompt_cache_key`。** Aster 0/842，pi 598/608，Codex 531/538。前缀未变的请求里 Aster 有 36 次完全零命中，且与请求间隔无关。213 万“前缀未变却未命中”中有 157 万发生在连续两次都未改写的请求之间，与裁剪无关。缓存键是否能消除这部分**尚未证实**，由 Stage 4 的指标判定。

已排除、不需要处理的因素：工具数组在 761 次相邻请求中从未变化；系统提示词 50 题完全一致；重复执行同一测试命令的 35 次里工作区全部发生过变化。

前两轮优化（10-11 计量校准、10-12 缓存键）分别单独验收、用四题总 token 做门槛，都未达标并已回退。单修计量时，改写减少的收益被“前缀未变却未命中”的增加抵消。所以本方案**三项改动一起交付**，用机制指标验收。

## 2. 设计总览

要建立的五条不变量：

| 编号 | 不变量 | 由哪个 Stage 实现 |
| --- | --- | --- |
| I1 | 同一会话的所有前台请求使用同一个 `prompt_cache_key` | 1 |
| I2 | 估算以服务端回执为准，误差在 5% 以内 | 2 |
| I3 | 未压缩时，本轮请求的历史是上一轮历史的追加 | 3 |
| I4 | 压缩是一次性事件：一次压缩只改写一次前缀，之后保持稳定 | 3 |
| I5 | 工具输出写入历史后内容不再变化，除非在压缩时被整体替换为占位符，且只替换一次 | 3 |

压缩采用“观测掩码”：保留模型自己的调用和推理记录，只把较早的工具输出正文替换成指向归档的占位符。不调用模型做摘要。依据见第 12 节。

涉及的文件：

| 文件 | 改动 | Stage |
| --- | --- | --- |
| `src/aster/providers/codec.py` | `request_payload` 增加 `prompt_cache_key` | 1 |
| `src/aster/providers/client.py` | `complete`、`_complete` 传递 `prompt_cache_key` | 1 |
| `src/aster/knowledge/maintenance.py` | 后台记忆任务使用独立的键 | 1 |
| `src/aster/core/runtime.py` | 传键；调用 `observe`；工具输出写入时限长；事件字段 | 1、2、3 |
| `src/aster/context/budget.py` | 回执计量；新压缩策略；`policy_version` | 1、2、3 |
| `src/aster/tools/workspace.py` | `read_file` 按字符上限返回整行 | 3 |
| `src/aster/training/env.py` | `policy_version` 默认值 | 2 |

## 3. 术语

- **prompt**：`render_prompt` 返回的列表，顺序为 `[system, 任务消息, 普通消息…, 历史项…, 尾部动态消息]`。
- **历史**：`Runtime._structured_history`，即 prompt 中的历史项部分。
- **组（group）**：`history_groups` 划分的一次完整交换（推理、调用和对应的全部输出）。用户消息单独成组。
- **尾部动态消息**：prompt 的最后一项，每轮重建，内容是 `workflow_state` 和记忆、技能等片段。它本来就不会被缓存。
- **最新交换**：含有 `function_call_output` 的最新一组；没有时取最后一组。永不掩码、永不丢弃。
- **limit**：`budget - output_reserve`，默认 64000 − 8192 = 55,808。
- **回执**：一次请求成功后服务端返回的 `input_tokens`、`output_tokens`。
- **锚（anchor）**：上一次成功请求的 prompt 与它的回执，用来估算下一次请求。

## 4. Stage 0：离线度量脚本

不改生产代码，不调用模型。

**新建** `evaluations/context-cache-20261013/metrics.py`。只读归档，输出每个 Agent 的机制指标（JSON 和表格）。

输入：`<runs>/<agent>-<task>/provider/` 下每个请求有 `<id>.json`（元数据和 `usage`）与 `<id>.request.json`（实际发送的请求体）。

计算规则（必须严格一致，否则对不上基线）：

1. 前台请求：排除 `tools` 名称列表恰为 `["memory_changes"]` 的请求。
2. 每个任务内按 `started` 升序。
3. 有回执：`usage.input_tokens > 0`。`cached = usage.input_tokens_details.cached_tokens`，缺失视为 0。
4. 后续请求：同一任务内，前面存在有回执的前台请求。“上一请求”指最近一个有回执的前台请求。
5. 每个输入项规范化为 `json.dumps(item, sort_keys=True, ensure_ascii=False)`，求当前请求与上一请求 `input` 列表的最长公共前缀长度 `lcp`。
6. `lcp >= len(prev_input) - 1` 记为**稳定**（上一请求的尾部动态消息本来就会被替换），否则记为**改写**。
7. 指标：
   - `rewrites`：改写请求数；`rewrite_uncached`：这些请求的 `input - cached` 之和。
   - `stable_miss`：稳定请求的 `max(0, min(prev_input_tokens, input_tokens) - cached)` 之和。
   - `stable_hit_mean`、`stable_hit_p10`：稳定请求的 `min(cached / prev_input_tokens, 1)` 的均值和第 10 百分位。
   - `input`、`cached`、`uncached = input - cached`、`cache_ratio = cached / input`。
   - `requests`（前台请求数）、`followups`（后续请求数）。
8. 支持 `--results <results.json> --exposure old20`，只统计 `exposure == "old20"` 的任务。

**验收**：对 `artifacts/private/general-coding-50-20261012/runs` 运行，输出必须与下表完全一致。

| 口径 | 指标 | Aster | pi | Codex |
| --- | --- | ---: | ---: | ---: |
| 50 题 | requests / followups | 825 / 761 | 608 / 553 | 538 / 481 |
| 50 题 | rewrites | 259 | 10 | 0 |
| 50 题 | rewrite_uncached | 5,408,244 | 232,582 | 0 |
| 50 题 | stable_miss | 2,134,725 | 761,086 | 734,182 |
| 50 题 | stable_hit_mean / p10 | 0.750 / 0.217 | 0.897 / 0.773 | 0.917 / 0.799 |
| 50 题 | uncached | 8,576,179 | 1,808,650 | 1,534,552 |
| 50 题 | cache_ratio | 0.480 | 0.855 | 0.872 |
| old20 | requests / followups | 394 / 368 | 240 / 218 | 254 / 229 |
| old20 | rewrites | 146 | 2 | 0 |
| old20 | rewrite_uncached | 2,787,012 | 32,140 | 0 |
| old20 | stable_miss | 1,109,691 | 346,461 | 307,560 |
| old20 | stable_hit_mean / p10 | 0.718 / 0.205 | 0.898 / 0.778 | 0.931 / 0.836 |
| old20 | input | 8,213,832 | 4,860,684 | 6,134,143 |
| old20 | uncached | 4,356,424 | 803,852 | 701,823 |
| old20 | cache_ratio | 0.470 | 0.835 | 0.886 |

## 5. Stage 1：缓存键

键的取值是 **`state.session_id`**，不是 `run_id`。原因：`continue_task`（用户每发一条新消息）会生成新的 `run_id`，但任务消息和历史原样保留、只在末尾追加，前缀可以复用；`session_id` 在整个会话内不变。Worker 有自己的 `RunState`，因而有自己的 `session_id`，天然分开。

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `codec.py` `request_payload` | 签名无此参数；Responses 分支发送 `model, input, tools, max_output_tokens, store` | 增加关键字参数 `prompt_cache_key: str \| None = None`。仅在 Responses 分支、且值不为 `None` 时写入 `payload["prompt_cache_key"]`。chat、anthropic 分支不写 |
| `client.py` `ModelClient.complete` | 签名 `(messages, tools, *, on_event=None)`，转调 `_complete` | 增加 `prompt_cache_key: str \| None = None` 并传给 `_complete` |
| `client.py` `ModelClient._complete` | 调用 `request_payload(...)` 构造线上请求 | 增加同名参数，传入该 `request_payload` 调用。这是唯一构造线上请求体的位置 |
| `runtime.py` `prepare_request` | `payload = request_payload(...)` 用于保存请求快照和哈希 | 加 `prompt_cache_key=state.session_id` |
| `runtime.py` `_model_turn` | `request_payload(...)` 用于计算 `request_hash` | 加 `prompt_cache_key=state.session_id` |
| `runtime.py` `_model_turn` | `self.model.complete(...)` 的 `ModelClient` 分支 | 加 `prompt_cache_key=state.session_id`。非 `ModelClient` 的分支不变 |
| `budget.py` `calculate` 内的 `tokens()` | `request_payload(...)` 用于计数 | 加 `prompt_cache_key=state.session_id`，使计数的请求与线上请求形状一致 |
| `maintenance.py` 构造任务处 | `"id": uuid4().hex` 内联生成；估算用的 `request_payload(...)` 无键 | 先 `job_id = uuid4().hex`，任务字典用它作 `"id"`；估算处加 `prompt_cache_key="memory-" + job_id` |
| `maintenance.py` 发送处 | 两处 `model.complete(job["messages"], job["tools"], ...)` | 都加 `prompt_cache_key="memory-" + job["id"]` |

可参考 `evaluations/checkpoint-cache-20261012/source.patch` 中 `codec.py`、`maintenance.py` 的 hunk（把 `run_id` 换成 `session_id`）。该补丁里 `client.py`、`runtime.py` 的 hunk 因 `0ce78db` 拆分了 `complete` 而无法直接应用；`checkpoint.py`、`navigation.py`、`evidence.py`、`workspace.py` 的 hunk 不属于本方案，不要应用。

**测试**：把 `artifacts/private/final-repair-20261012/candidate/tests/test_prompt_cache.py` 复制到 `tests/`，并按新的键语义修改：

- 期望的键由 `run_id` 改为 `session_id`。
- `continue_task` 之后键**保持不变**；`start(TaskSpec(...))` 开新任务后键不同。
- 后台记忆请求的键为 `"memory-" + job_id`，且与前台不同。
- chat、anthropic 协议的请求体里没有该字段。

已有测试中用 `request_payload(...)` 重算请求体并与 `estimated_tokens` 或保存的请求快照比较的断言（如 `tests/test_model_context_config.py`），需要在重算时传入同一个键。

**验收**：上述测试通过；全量测试相对基线无新增失败。

## 6. Stage 2：以回执为准的计量

### 6.1 为什么换估算方式

对 50 题归档离线回放三种估算，比较“估算值 / 服务端 `input_tokens`”（实际输入 >15k 的请求）：

| 估算方式 | 中位数 | p95 | 最大 |
| --- | ---: | ---: | ---: |
| 现状：本地 `cl100k` 计整个请求 | 1.78 | 2.84 | 3.46 |
| 10-11 补丁：密文置空 + 已知推理 token + 加性开销 | 1.31 | 1.45 | 1.52 |
| 以上一请求回执为锚，只估本轮新增项 | **1.004** | **1.026** | 1.047 |

所以主估算用“锚”，10-11 补丁的做法只在没有锚时作为回退。

另外两条实测事实，决定回退估算的形式：

- 服务端给每个请求固定计入约 5,005 token 的 instructions（Aster 自己的系统提示词约 3,290 字符），811 个请求全部一致。供应商开销是**加性常数**，不要做成比例。
- 推理项密文的服务端成本：1,421 项合计 25.4 字符/token，最密的一项 14 字符/token。

### 6.2 `BudgetCalculator` 的改动

**第一步：应用 10-11 补丁。** 把 `evaluations/general-coding-cost-20261011/source.patch` 中 `budget.py` 的全部 hunk 应用到 `src/aster/context/budget.py`（该文件自 `c6b651d` 起未变，可直接应用）。得到：

- `__init__` 新增 `_scope`、`_reasoning_tokens`、`_provider_overhead`。
- `request_tokens(payload, *, overhead=True)`：把已知推理项的 `encrypted_content` 在副本里置空后计数，加回该项已报告的推理 token，再加 `_provider_overhead`。线上字节不变。
- `observe(payload, reply)`：只接受干净的回执，更新 `_provider_overhead`（取观察到的最大正差值）和 `_reasoning_tokens`。
- `calculate` 开头按 scope 清空校准；`tokens()` 改用 `request_tokens`；报告的 `sections` 增加 `provider_overhead`。

应用时做三处调整：

1. `policy_version` 直接改为 `"deterministic-v9"`（不要用 v8）。同步修改 `src/aster/training/env.py` 中的默认值，以及 `tests/test_stable_prefix.py`、`tests/test_context_policy_input.py` 中的断言。
2. scope 由 `(state.run_id, protocol, model)` 改为 `(state.session_id, protocol, model)`。
3. **不要应用**该补丁里 `prompt.py` 的 hunk（它改写系统提示词，与本方案无关）。`runtime.py` 的 hunk 已过时，按 6.3 手工实现。

补丁中循环相关的改动（`target` 等）会在 Stage 3 被整体替换，这一步照原样应用即可。

**第二步：增加锚。**

- `__init__` 增加 `self._anchor = None`。scope 变化时一并置 `None`。
- `observe` 签名改为 `observe(payload, reply, prompt=None)`，行为：
  - 回执不干净（`classification != "success"`、`flags` 非空、`usage_requests != 1`、`input_tokens` 或 `output_tokens` 不是合法整数）：`self._anchor = None` 后返回。
  - 回执干净且 `prompt` 不为 `None`：在其余逻辑之前设置

    ```python
    self._anchor = {
        "items": [canon(x) for x in prompt[:-1]],   # 不含尾部动态消息
        "tail": self.count(prompt[-1]),
        "tokens": inputs + outputs,
    }
    ```

    其中 `canon(x) = json.dumps(x, sort_keys=True, ensure_ascii=False)`。
- 新增 `estimate(prompt, payload) -> tuple[int, str]`：

  ```python
  def estimate(self, prompt, payload):
      anchor = self._anchor
      if anchor is not None:
          n = len(anchor["items"])
          if [canon(x) for x in prompt[:n]] == anchor["items"] and len(prompt) > n:
              added = sum(self.count(x) for x in prompt[n:-1] if not from_model(x))
              tail = max(0, self.count(prompt[-1]) - anchor["tail"])
              return anchor["tokens"] + added + tail, "receipt"
      return self.request_tokens(payload), "local"
  ```

  `from_model(x)` 为真当且仅当 `x.get("role") == "assistant"` 或 `x.get("type") in {"reasoning", "function_call", "message"}`。这些项来自上一次回复，其 token 已包含在回执的 `output_tokens` 里；其余新增项（工具输出、用户消息）用本地计数。

- `ContextReport` 增加字段（均带默认值）：`estimate_basis: str = "local"`、`prefix_drift: bool = False`、`first_changed_item: int | None = None`、`masked_outputs: int = 0`。
- `calculate` 中，对初次渲染的 prompt 调用 `estimate`，结果记为 `before` 和 `estimate_basis`。同时计算漂移：锚存在但 prompt 不是它的追加时，`prefix_drift = True`，`first_changed_item` 为第一个不同项的下标。
- Stage 2 暂时保留补丁后的裁剪循环，只把循环前的 `before` 换成 `estimate` 的结果；循环内部仍用 `request_tokens`。

### 6.3 `runtime.py` 的改动

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `_model_turn` 开头 | `request_hash = self._request_hash(request_payload(...))`，请求体用完即弃 | 先 `payload = request_payload(...)`，再 `request_hash = self._request_hash(payload)` |
| `_model_turn` 中累加 `state.usage` 之后 | 无 | `self.context.calculator.observe(payload, reply, prompt)`。两种模型分支都调用；`ModelReply.diagnostics` 默认是空字典，测试用模型是安全的 |
| `prepare_request` 发出 `context_built` 事件处 | 字段到 `request_sha256` 为止 | 增加 `estimate_basis`、`prefix_drift`、`first_changed_item`、`masked_outputs` |

`self._request_estimate = report.estimated_tokens + self.settings.effective_output_tokens` **保持不变**。锚估算的构造保证它不低于上一次的实际输入，所以无需为 `TeamBudget` 另设保守值。

确认流式路径（`providers/streaming.py`）返回的 `reply.diagnostics` 来自 `RequestAccounting`，且只含成功那次尝试的用量。

### 6.4 测试与验收

1. 把 `artifacts/private/final-repair-20261012/candidate/tests/test_context_cost.py` 复制到 `tests/`。前三个测试应不经修改通过；第四个（压缩留余量）在 Stage 3 之后仍须通过。
2. 新增单元测试，覆盖：
   - 干净回执后，下一次 `estimate` 返回 `"receipt"`，值等于 `tokens + 新增非模型项计数 + 尾部增量`。
   - 历史被改动（任一旧项内容变化）后回退为 `"local"`，且 `prefix_drift` 为真、`first_changed_item` 正确。
   - 不干净的回执清除锚。
   - `session_id` 不变的续接（`continue_task`）保留锚；`model` 变化清除锚。
3. **新建** `evaluations/context-cache-20261013/replay_metering.py`：对归档逐任务回放真实的 `BudgetCalculator`。每个前台请求先用 `estimate(prompt, payload)` 估算（Responses 协议下 `prompt` 就是请求体的 `input`），与回执的 `input_tokens` 比较，再用 `ModelReply(usage={"usage_requests": 1, "input_tokens": …, "output_tokens": …})` 调用 `observe`。

**验收**（对 50 题 Aster 归档，统计 `basis == "receipt"` 的请求）：

| 指标 | 要求 | 参考回放值 |
| --- | --- | --- |
| 估算 / 实际 的中位数 | ≤ 1.02 | 1.005 |
| p95 | ≤ 1.05 | 1.029 |
| 最大值 | ≤ 1.15 | 1.107 |
| 最小值 | ≥ 0.99 | 0.999 |
| 稳定的后续请求中 `basis == "receipt"` 的比例 | ≥ 95% | 499 / 502 |

并且 `tests/test_worker_token_handoff.py`、`tests/test_interaction_workers.py`、`tests/test_usage_budget.py`、`tests/test_memory_admission.py` 相对基线无新增失败。

## 7. Stage 3：只追加历史与一次性压缩

### 7.1 阈值

全部由 `limit` 推出，写成 `budget.py` 顶部的具名常量或局部变量，不加配置项。

| 名称 | 公式 | 默认值 | 含义 |
| --- | --- | ---: | --- |
| `trigger` | `limit - min(output_reserve, limit // 8)` | 48,832 | 估算超过它才压缩 |
| `keep_recent` | `limit // 8` | 6,976 | 压缩时保留原文的最新工具输出总量 |
| `relief` | `limit * 3 // 4` | 41,856 | 掩码后仍高于它，才丢弃旧组 |
| `drop_target` | `limit // 2` | 27,904 | 丢弃旧组的目标 |

按 50 题的投影，不裁剪时上下文峰值中位数为 23,565，只有 3 题超过 `trigger`。**大多数任务一次都不压缩，这是预期结果。**

### 7.2 `BudgetCalculator.calculate` 的新流程

替换现有的 `while tokens(prompt) > limit` 循环之前的判定逻辑。归档遍历（为每个 `function_call_output` 建立 `references`）保持不变。

```python
prompt = render()
full_before = full(prompt)                    # request_tokens(request_payload(...))
before, basis = self.estimate(prompt, payload_of(prompt))
anchored = basis == "receipt"
scale = min(1.0, before / full_before) if anchored else 1.0

def tokens(p):                                # 压缩过程中使用
    return math.ceil(full(p) * scale)

if before > (trigger if anchored else limit):
    mask_old_outputs()                        # 阶段一
    if tokens(prompt) > relief:
        drop_old_groups()                     # 阶段二
    while tokens(prompt) > limit:             # 阶段三：现有循环体，原样保留
        ...
```

说明：

- 没有锚时本地估算已知偏高，所以用 `limit` 而不是 `trigger` 作阈值，避免进程重启后的第一个请求被无谓压缩。
- `scale` 用当前这一刻“锚估算 / 本地全量计数”的比值修正压缩过程中的本地计数。它只会偏保守。

**阶段一：掩码（`mask_old_outputs`）。**

1. 按从新到旧的顺序遍历所有 `function_call_output` 项，累加正文的 `self.count(content)`。
2. 最新交换里的输出永远保留原文，并计入累加值。
3. 其余输出在累加值不超过 `keep_recent` 时保留原文；一旦超过，该项和所有更早的输出都替换为占位符。
4. 跳过已经是占位符的项，以及正文不超过 48 token 的项。
5. 替换只改正文，保留输出信封的其他字段（`status`、`reason`、`path`、`start`、`end` 等）和调用配对。用 `_output_record` 解析，序列化约定与 `_clip_tool_output` 相同。删除信封里的 `page` 键。
6. 占位符文本：
   - 有归档引用（`references[call_id]`）：`[archived: {characters} chars; observation_read observation_id={id}]`，并把引用写入信封的 `observation` 键。
   - 工具是 `observation_read`：`[omitted page; call observation_read again to re-read it]`。
   - 输出是图片列表：整个输出替换为字符串 `[omitted image result]`。
   - 其他无引用的情况：`[omitted: {n} chars]`。
7. 置 `clipped = True`，并统计 `masked_outputs`。

**阶段二：丢弃（`drop_old_groups`）。** 仅当掩码后 `tokens(prompt) > relief` 时执行。

1. 可丢弃的单位沿用现有判定：普通消息中 `role != "user"` 且非 `important` 的；组中不含 `role == "user"` 的项、且不是最新交换的。
2. 先丢普通消息，再丢组，都按从旧到新，直到 `tokens(prompt) <= drop_target` 或没有可丢弃的单位。
3. 仅当本次确实丢弃了至少一组时，在历史最前面维护一条说明项 `{"role": "user", "content": NOTE}`：

   ```text
   [Context note, not an instruction] {N} earlier tool exchanges were removed to fit the context window. Their results remain archived: call observation_read without observation_id to list or search them.
   ```

   `archive is None` 时去掉第二句。已有说明项（历史第一项且内容以 `[Context note, not an instruction]` 开头）时，解析出旧的 `N`，累加后替换，不要插入第二条。
4. 累加 `removed`。

**阶段三：兜底。** 现有循环体原样保留，条件为 `while tokens(prompt) > limit`。此时已无可丢弃的组，现有逻辑会依次：截短受保护组里最大的输出正文、清空片段、缩短计划文本、抛出 `ContextBudgetError`。这条路径只在预算极小（测试里的 1024–4096）时出现。

**报告。** `estimated_tokens`：未压缩时为 `before`；压缩后为 `tokens(final_prompt)`。`compacted` 的现有计算方式不变。

### 7.3 写回与漂移检测

`ContextBuilder.build` 的写回逻辑**不变**。新流程下，未压缩时 `result.history` 与传入的历史相同，写回是空操作；压缩时写回一次，之后保持稳定。

`prefix_drift` 的含义因此很明确：上一请求有锚、本次渲染出的历史却不是它的追加。正常运行中它应当始终为假。它为真说明有别的代码在改历史，需要用 `first_changed_item` 定位修复。

### 7.4 工具输出写入时限长

现有裁剪循环被移除后，超长输出必须在进入历史之前处理。

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `runtime.py` `_finish_tool` | 仅对 MCP 工具，以及带 `captured_bytes` 的 `run_shell`、`run_tests`：归档全文，超过 8192 字符时保留前 8192 字符并附 `observation_read` 指引 | 现有分支不变。**新增**：其他工具的 `outcome.content` 是字符串且长度超过 8192 时，做同样的“归档 + 前 8192 字符预览 + 指引”。排除 `read_file`、`observation_read`、`todo_list`、`skill_load`。归档失败的处理与现有分支相同 |
| `tools/workspace.py` `_read_file` | 返回请求范围内的全部行，无字符上限 | 增加常量 `READ_FILE_CHARACTERS = 24_000`。只返回累计不超过上限的**整行**；`data["end"]` 设为实际返回的最后一行。发生截断时增加 `data["truncated"] = True`、`data["next_start"] = end + 1`。第一行本身超限时返回该行的前 24,000 字符 |
| `runtime.py` `_append_output` | `read_file` 的信封含 `path`、`start`、`end`、`source_sha256` | `truncated`、`next_start` 存在时一并写入信封 |

`read_file` 不走归档：文件本身就是来源，模型用 `start=next_start` 继续读即可。`end` 必须如实反映可见范围，因为 `tools/workflow.py` 用它校验选区。

### 7.5 测试

**新建** `tests/test_append_only_context.py`，至少包含：

1. **40 轮不变量测试。** 用 `ContextBuilder(16000, 1024)`，每轮向历史追加一次交换（一个 `function_call` 和一个约 1,500 字符的输出），调用 `build`，再用一个确定性的“假服务端计数”（例如 `4000 + Σ len(json(item)) // 4`）构造干净回执调用 `observe`。调整参数，使累计增长恰好越过 `trigger` 一次。断言：
   - 整个过程中“上一轮 `prompt[:-1]` 不是本轮 `prompt[:-1]` 的前缀”恰好发生 **1 次**，且那一轮 `report.compacted` 为真。
   - 其余各轮 `report.compacted` 为假、`report.prefix_drift` 为假。
   - 每个 `call_id` 的输出在整个过程中至多出现 2 种取值（原文、占位符），变成占位符后不再变化。
   - 最新交换的输出始终是原文。
   - 每轮 `history_groups(history)` 都合法。
   - `basis == "receipt"` 的各轮，`estimated_tokens` 与假服务端对同一 prompt 的计数相差不超过 10%。
2. **掩码可回读。** 提供真实的 `archive`（`EvidenceStore.archive_observation`），压缩后对每个占位符里的 id 调用 `read_observation`，能读回原文。
3. **阶段二。** 构造调用参数很大、输出很小的历史，使掩码后仍高于 `relief`。断言旧组被丢弃到 `drop_target` 以下，历史第一项是说明项；再触发一次压缩后仍只有一条说明项，且 `N` 累加。
4. **无锚阈值。** 没有锚时，本地估算介于 `trigger` 与 `limit` 之间不触发压缩。
5. **写入时限长。** 超过 8192 字符的 `git_diff` 结果被归档并预览；超过 24,000 字符的 `read_file` 返回整行、`end` 正确、带 `truncated` 和 `next_start`。

既有测试的处理规则见第 9 节。

**验收**：新测试通过；`tests/test_context_cost.py` 四个测试通过；全量测试相对基线无新增失败（按第 9 节规则修改过的断言需在提交说明里逐条列出）。

## 8. Stage 4：评测与验收

**先停下，等用户确认后再运行。**

准备工作（不调用模型）：

1. 新建 `evaluations/context-cache-20261013/PROTOCOL.md`，运行前冻结。
2. 条件与 50 题评测一致：`gpt-5.6-luna`、Responses、medium、64000 上下文、8192 输出上限、900 秒/题、4 CPU / 8 GiB、6 槽并发、空会话、单 Agent、自动记忆开启。复用 `evaluations/general-coding-50-20261012/` 的运行器、网关和独立评分器。
3. 任务：50 题中 `exposure == "old20"` 的 20 题。只重跑 Aster，每题一次。pi、Codex 使用 50 题评测中这 20 题的已有结果，不重跑。
4. 保留全部失败、超时和缺失用量，不追加采样、不择优。

指标用 Stage 0 的 `metrics.py` 从新归档计算，基线是第 4 节 old20 行的 Aster 数值。

| 机制 | 指标 | 基线 | 要求 |
| --- | --- | ---: | ---: |
| 只追加 | `rewrites / followups` | 146 / 368（39.7%） | ≤ 3% |
| 只追加 | `rewrite_uncached` | 2,787,012 | ≤ 250,000 |
| 只追加 | `prefix_drift` 为真的 `context_built` 事件数 | — | 0 |
| 只追加 | 每次改写都对应一次 `compacted` 为真 | — | 是 |
| 缓存键 | `stable_hit_mean` | 0.718 | ≥ 0.85 |
| 计量 | `basis == "receipt"` 请求的估算 / 实际，中位数与 p95 | — | ≤ 1.02、≤ 1.05 |
| 总体 | `cache_ratio` | 0.470 | ≥ 0.78 |
| 总体 | `uncached` | 4,356,424 | ≤ 1,300,000 |
| 总体 | `input` | 8,213,832 | ≤ 9,450,000（+15%） |
| 质量 | 独立通过数 | 15 / 20 | ≥ 15 |
| 质量 | 前台请求数 | 394 | 报告，不设硬门槛 |

通过数的判定：15 及以上通过。14 时逐题审查由通过变为未通过的任务，只有轨迹证明与上下文改动无关（例如供应商错误）才可接受。13 及以下不通过。

总输入必须与未缓存输入**一起报告**。只追加会让单次请求变大，缓存输入虽然便宜但仍占窗口。

结果判读：

| 观察到 | 结论 | 下一步 |
| --- | --- | --- |
| 各项达标 | 方案生效 | 更新文档；由用户决定是否重跑完整 50 题 |
| 改写达标，`stable_hit_mean` 仍低于 0.80 | 缓存键没有改善路由 | 问题在传输层。**不要继续调上下文策略。** 排查方向：仿照 pi 增加与键一致的 `session_id`、`x-client-request-id` 请求头；只重放回复中确实返回过的推理密文 |
| 改写率高于 3% | 不变量 I3 被破坏 | 查 `prefix_drift` 事件的 `first_changed_item`，定位改历史的代码 |
| 通过数下降，且翻转题发生过压缩 | 掩码丢失了必要信息 | 调大 `keep_recent`；检查模型是否调用了 `observation_read` |
| `input` 增幅超过 15% | 压缩过晚或过弱 | 调低 `trigger` 或 `relief` |

## 9. 既有测试的处理规则

必须**不经修改**继续通过的断言：

- `tests/test_stable_prefix.py`：动态内容只出现在尾部消息；`first[:-1] == second[:-1]`；追加一次交换后线上前缀不变。
- 调用与输出配对完整（`history_groups` 合法）；最新交换保留；用户消息与 `important` 消息保留。
- 归档可回读、跨运行隔离、损坏时停止：`tests/test_observation_archive.py`、`tests/test_context_archives.py` 中的相应断言。
- `calculate` 不修改入参，只有 `build` 提交：`tests/test_context_state.py`。
- `estimated_tokens <= budget`：`tests/test_robustness_benchmark.py`。
- 预算不足时抛出 `ContextBudgetError`，且不截断当前任务。

允许修改的断言（只限这几类，并在提交说明中逐条列出原断言、新断言和原因）：

- `policy_version` 字符串。
- 重算请求体时需要补上 `prompt_cache_key`。
- 编码了旧策略具体行为的断言：例如“超预算后恰好移除了某几组”“某个旧输出被截短到某长度”“`removed_messages > 0`”。新策略下较早的输出是被掩码而不是被移除。改写时保留该测试原本要保护的性质。

不允许为了通过测试而放宽保护性断言，或修改生产代码去迎合旧策略的具体行为。

## 10. 不要改

- `render_prompt` 的顺序和尾部动态消息的位置；`system_prompt` 的文本；工具的名称、描述和参数定义。它们决定前缀字节，任何改动都会让本轮结果与基线不可比。
- `codec.py` 的 chat、anthropic 分支；`store`；不要添加 `include`。
- 检查点格式、`checkpoint_version`、`RunState` 的字段（它是 `extra="forbid"`）。本方案不需要持久化任何新状态：占位符和说明项就在历史里，锚和校准只存在于内存。
- `EvidenceStore.archive_observation`、`read_observation` 的格式与语义；`observation_read` 工具。
- `calculate` 中压缩之前的归档遍历。
- `TeamBudget`、Worker 交接逻辑、`_request_estimate` 的公式。
- 不引入模型摘要调用。
- 不修改任何已有的评测结果、报告和归档。

## 11. 已知取舍

- **进程重启后的第一个请求没有锚**，使用偏高的本地估算。阈值已放宽到 `limit`，但历史很长时仍可能提前压缩一次。重启很少见，且那时缓存多半已过期，接受这一点。
- **阶段二会丢掉较早的调用记录**，只留一条计数说明。它只在掩码不足以腾出空间时发生，50 题里预计不会出现。
- **`read_file` 单次最多返回 24,000 字符。** 50 题终态请求里 `read_file` 输出的 p90 为 6,661 字符、最大 14,276 字符，预计不影响现有行为。
- **缓存键的效果未经证实。** 它是否改善命中，只能由 Stage 4 的 `stable_hit_mean` 判定。

## 12. 设计依据

- **只追加、压缩低频且一次到位**：pi 在实际用量超过 `窗口 − 16384` 时才压缩，一次压到“摘要 + 最近 20000 token”，会话日志只追加（`pi-coding-agent/dist/core/compaction/compaction.js`，1.1.0）。
- **以最近一次服务端用量为基准，只估算其后的新增内容**：pi 的 `estimateContextTokens`；Codex 的 `get_total_token_usage`（`codex-rs/core/src/context_manager/history.rs`）。
- **按会话发送 `prompt_cache_key`**：pi 的 `openai-responses.ts`；Codex 的 `client.rs`。
- **工具输出只在写入时截断一次**：pi 的 `truncate.js`（2000 行 / 50KB）；Codex 的 `record_item_with_metadata`。
- **用占位符替换较早的工具输出、保留最近若干条**：Claude Code 的 microcompact（依据第三方整理的源码 `claude-code-best/claude-code`，非官方来源）。
- **观测掩码与模型摘要的对照**：*The Complexity Trap*（[arXiv:2508.21433](https://arxiv.org/abs/2508.21433)，NeurIPS 2025 DL4Code）在 SWE-agent 与 SWE-bench Verified 上比较了两者：成本都约为不压缩的一半，通过率相当；摘要还会让轨迹变长。Aster 的轮次本已偏多，因此不引入摘要。
- 如果掩码在长任务上确实降低通过率，下一步参考 ACON（[arXiv:2510.00615](https://arxiv.org/abs/2510.00615)）的失败驱动压缩指南，作为独立的策略版本评估，不在本方案内。

相关的历史记录：[2026-10-11 计量校准](cost-optimization-20261011.md)、[2026-10-12 成本诊断](../evaluations/cost-diagnosis-20261012/REPORT.md)、[2026-10-12 检查点与缓存键](../evaluations/checkpoint-cache-20261012/REPORT.md)、[50 题评测](../evaluations/general-coding-50-20261012/REPORT.md)。
