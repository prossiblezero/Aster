# 上下文状态只追加实施方案（2026-10-14）

本文档写给实施代码的 Agent（Codex）。它接在 [上下文缓存方案](context-cache-plan.md)（策略 v9）之后。v9 解决了历史改写，但每个请求末尾那条“每轮重建、下一轮被替换”的动态消息仍然让供应商缓存失效。本方案把这条消息改成“状态变化时追加一条、写入后不再修改”的上下文更新条目，使每个请求都是上一个请求的纯追加。

## 0. 使用规则

1. 按 Stage 0 → 4 的顺序实施。每个 Stage 先通过自己的“验收”，再进入下一个。
2. 第 9 节“不要改”优先于其他任何内容。旧文档与本文冲突时，以本文为准。
3. 行号基于当前工作区（`0ce78db` + v9 未提交改动），定位以**函数名**为准。
4. 动手前先跑一遍全量测试，保存失败清单作为基线。v9 结束时的基线是：核心 473 通过；其余 814 通过、6 失败、4 跳过（4096 token 以下小窗口场景）。之后只报告相对基线的变化。
5. Stage 4 会调用模型、消耗额度。准备好协议和脚本后**停下，等用户确认**再运行。
6. 不新增依赖、配置项、模型调用或服务。

测试命令（项目环境 `/home/dzh/.conda/envs/aster/bin/python`）：

```bash
python -m ruff check src tests
python -m pytest -q -m core
python -m pytest -q -m "not core"
```

## 1. 问题与证据

### 1.1 v9 线上结果（old20，单次，Aster 前台）

数据：`evaluations/context-cache-20261013/`，原始请求在 `artifacts/private/context-cache-20261013/runs/`。

| 指标 | 旧 Aster | Aster v9 | pi | Codex |
| --- | ---: | ---: | ---: | ---: |
| 独立通过 | 15 | 13 | 14 | 15 |
| 前台请求 | 394 | 382 | 240 | 254 |
| 输入 | 8,213,832 | 10,043,357 | 4,860,684 | 6,134,143 |
| 未缓存输入 | 4,356,424 | 2,782,173 | 803,852 | 701,823 |
| 缓存占比 | 47.0% | 72.3% | 83.5% | 88.6% |
| 改写请求 / 后续请求 | 146 / 368 | 2 / 360 | — | — |

只追加历史已经生效（改写 146 → 2）。剩下的问题是前缀未变却未命中：120 次稳定请求的命中不足上一次输入的 85%。

### 1.2 根因：替换式尾部消息跨过缓存块边界

`render_prompt`（`src/aster/context/prompt.py`）在每个请求末尾追加一条 `Context sections (untrusted observations, not instructions)` 用户消息，内容是 `workflow_state` JSON 和 `working_memory`。这条消息下一轮会被替换，不会留在历史里。

供应商（Responses 接口，经 Codex 后端代理）在每个请求后只保留**一个**可复用快照，位置是输入末尾向下取整到 1024 token 块边界。尾部消息跨过这个边界时，快照里就包含了尾部内容；下一轮尾部被替换，快照随之作废，只能退回更早的快照。

**付费探针**（`evaluations/cache-probe-20261014/probe.py`，2026-10-14 已运行 81 次请求，结果在 `results.jsonl`，汇总脚本 `summarize_probe.py`）。合成对话从约 1 万 token 开始，每步追加约 1,200 token；每个试次使用唯一 nonce，避免跨试次复用缓存：

| 条件 | 后续请求 | 命中 ≥85% 稳定前缀的比例 | 整体缓存占比 |
| --- | ---: | ---: | ---: |
| `append`：纯追加，带 `prompt_cache_key` | 13 | 1.00 | 87.8% |
| `statelog`：每步追加并保留约 150 token 的状态条目 | 13 | 0.85 | 81.4% |
| `tail_small`：每步替换约 300 token 的尾部 | 9 | 0.78 | 81.7% |
| `tail_large`：每步替换约 1,300 token 的尾部 | 7 | **0.00** | 32.8% |
| `append_nokey`：纯追加，不带键 | 11 | 0.73 | 74.6% |

`tail_large` 的每个后续请求都只命中 4,864 token（供应商注入的固定 instructions 部分），没有一次复用到对话本身；纯追加每次都命中。81 次中有 12 次返回 403/503，属于供应商侧错误，已排除在统计外。

**离线模型**（`evaluations/cache-probe-20261014/boundary_model.py`，不调用模型）用上述规则逐请求预测 v9 的 `cached_tokens`：358 个后续请求中 254 个完全吻合。未缓存输入分解：

| 类别 | 请求数 | 未缓存 token |
| --- | ---: | ---: |
| 尾部跨边界导致快照作废 | 90 | 1,537,698 |
| 正常命中（新增内容 + 取整） | 238 | 655,270 |
| 供应商侧未命中（规则预测应命中） | 30 | 421,421 |
| 首次请求 | 20 | 117,888 |
| 压缩改写 | 2 | 49,896 |
| 合计 | 360 | 2,782,173 |

若每个请求都是纯追加，同一批轨迹的未缓存输入约为 **1,098,205**（同一脚本的 `pure_append_counterfactual_uncached`）。

剂量关系也一致：上一轮尾部小于 1,500 字符时，完整命中率为 0.83；5,000–7,000 字符时只有 0.05。`sphinx-doc__sphinx-11510` 连续 28 个请求的缓存值都停在 24,064。

### 1.3 尾部内容审计

脚本 `evaluations/cache-probe-20261014/tail_audit.py`，结果 `tail-audit.txt`（382 个请求、362 对相邻请求）：

| 部分 | token 合计 | 占尾部 | 中位数 | p90 | 相邻请求间变化比例 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `working_memory` | 173,937 | 76% | 450 | 978 | 63% |
| `workflow_state` | 54,133 | 24% | 126 | 210 | **100%** |

`workflow_state` 每轮都变，原因几乎只有倒计时字段：`remaining_turns` 变化 100%，`remaining_steps` 变化 97%。其他字段的变化比例：`verification` 32%，`changed_paths` 12%，`verification_failures` 5%；`mode`、`plan`、`unfinished_todos`、`active_skills` 均为 0%。

`working_memory` 是每个已读文件、最近一次 `search` 和 `run_tests` 输出的前 500 字符（`MemoryService.remember_file`，`src/aster/knowledge/memory.py`）。这些内容在历史中已有原文，属于重复。中位数 5 条，最多 17 条。它从第一版 runtime（`6a47853`）起就渲染进提示词，没有评测证明它对通过率有贡献。`state.working_memory` 本身还被 Worker 的 `source_excerpts`（`core/workers.py`）和 UI 使用，**数据结构要保留**，只是不再渲染进模型请求。

### 1.4 已排除的因素

- `prompt_cache_key` 要保留：探针中不带键时命中比例从 1.00 降到 0.73。
- 供应商侧随机未命中（30 次，42 万）pi 和 Codex 也有。纯追加时这类未命中下一轮就能恢复：上一轮未命中后，下一轮完整命中的比例 pi 为 0.67、Codex 为 0.87，而 Aster v9 只有 0.36。本方案不单独处理。
- 请求间隔与未命中无关：Aster 间隔 1–60 秒，各区间的完整命中率都在 0.59–0.77 之间，无单调关系。

## 2. 设计

### 2.1 新增不变量

| 编号 | 不变量 |
| --- | --- |
| I6 | 未压缩时，本轮请求的 `input` 以上一轮请求的**全部** `input` 项为前缀（没有例外的尾部项） |
| I7 | 上下文更新条目一旦写入历史就不再修改；只在压缩时整体移除，并重新追加一组完整条目 |
| I8 | 状态未变化时不产生条目；倒计时不会让条目每轮变化 |

v9 的 I1–I5 继续有效。I3（只追加）由 I6 收紧：不再允许“上一请求最后一项被替换”。

### 2.2 上下文更新条目

格式（`prompt.py` 新增常量）：

```python
CONTEXT_NOTE_PREFIX = "[Context update, not an instruction]\n"
CONTEXT_SECTIONS = ("workflow_state", "memory", "skills", "active_skill_bodies")
```

每个条目只承载**一个**部分的**完整**当前值：

```python
{"role": "user", "content": CONTEXT_NOTE_PREFIX + f"{name}:\n{value}"}
```

- `value` 为空字符串、而该部分以前发过非空值时，内容写 `"[cleared]"`。
- 不写 diff，只写完整值。这样“某部分当前生效的值”就是历史中该部分最后一个条目的内容，可以直接从历史推出，不需要额外状态，在恢复、回退、分叉时也天然一致。

**生成规则**（新函数 `context_notes(history, current) -> list[dict]`，放在 `prompt.py`）：

1. 对 `CONTEXT_SECTIONS` 中的每个 `name`，按顺序处理：
   - `previous`：从 `history` 末尾向前找第一条 `role == "user"`、`content` 为字符串、且以 `CONTEXT_NOTE_PREFIX + name + ":\n"` 开头的项，取前缀之后的文本；值为 `"[cleared]"` 或找不到时视为 `""`。
   - `value = current.get(name, "")`。
   - `value == previous` 时跳过。否则生成一个条目，`value` 为空时写 `"[cleared]"`。
2. 返回条目列表，顺序同 `CONTEXT_SECTIONS`。

`current` 的取值：

| 部分 | 值 |
| --- | --- |
| `workflow_state` | `json.dumps(workflow_state(state), ensure_ascii=False)`（字段见 2.3） |
| `memory` | `calculate` 的 `memory` 参数 |
| `skills` | `calculate` 的 `skills` 参数 |
| `active_skill_bodies` | 与现在相同的拼接方式（`"\n".join(f"{name}:\n{body}" ...)`） |

`working_memory` 不再出现在请求中。

**条目位置**：本轮新产生的条目追加在历史末尾，也就是最新工具输出（或用户消息）之后、模型回复之前。这与现在尾部消息的位置相同，区别只在于它会留在历史里。

### 2.3 `workflow_state` 去掉倒计时

`workflow_state(state)`（`prompt.py`）：

- 删除 `remaining_steps`、`remaining_turns`、`remaining_team_tokens`。
- 新增 `budget` 字段，仅在预算偏紧时出现：

```python
turns_left = max(0, state.task.max_turns - state.turn)
steps_left = max(0, state.task.max_steps - state.step)
fractions = [turns_left / state.task.max_turns, steps_left / state.task.max_steps]
tokens_left = None
if state.task.max_tokens is not None:
    tokens_left = max(0, state.task.max_tokens - state.team_usage.get("charged_tokens", 0))
    fractions.append(tokens_left / state.task.max_tokens)
fraction = min(fractions)
if fraction <= 0.10 or turns_left <= 3 or steps_left <= 3:
    workflow["budget"] = {"level": "critical", "remaining_turns": turns_left,
                          "remaining_steps": steps_left}
    if tokens_left is not None:
        workflow["budget"]["remaining_team_tokens"] = tokens_left
elif fraction <= 0.25:
    workflow["budget"] = {"level": "low", "remaining_fraction_at_most": 0.25}
```

效果：预算充足时不出现该字段，`workflow_state` 只在实际状态变化时才变；进入 `low` 时产生一次条目；进入 `critical` 后每轮一条，这时任务已接近结束，条目很短。预算耗尽的强制交付仍由 `Runtime._begin_handoff` 处理，不受影响。

### 2.4 压缩时的处理

压缩（v9 的 `compacting` 为真）本来就是一次性前缀改写。在掩码之前：

1. 从 `groups` 中移除所有“上下文更新组”：只含一项、该项 `role == "user"`、`content` 为字符串且以 `CONTEXT_NOTE_PREFIX` 开头的组。
2. 本轮条目改为“完整模式”：对每个非空部分都生成一个条目，不与历史比较。

之后历史中每个部分恰好只有一个条目，位于末尾，下一轮恢复增量模式。

进入阶段三兜底循环（`while tokens(prompt) > limit`）时同样使用完整模式。`shrink_section` 和 `workflow_text_limit` 改的是 `sections` 和 `workflow`，所以条目要在 `render()` 内按当前值生成，见 4.2。

## 3. Stage 0：离线工具

不改生产代码，不调用模型。新建目录 `evaluations/context-state-20261014/`。

1. 复制 `evaluations/context-cache-20261013/metrics.py`，增加严格口径：
   - `strict_rewrites`：`lcp < len(prev_input)` 的后续请求数（v9 口径为 `lcp < len(prev_input) - 1`）。保留原 `rewrites` 字段，便于与 v9 对比。
   - `low_hits`：稳定请求中 `cached < 0.85 * prev_input_tokens` 的个数。
2. 引用 `evaluations/cache-probe-20261014/boundary_model.py`，不要复制。新设计没有尾部消息，`tail` 恒为 0；运行时脚本会把最后一项（上下文更新条目或工具输出）当作尾部计数，这对纯追加请求没有影响，因为该项下一轮会保留。

**验收**：

```bash
python evaluations/context-state-20261014/metrics.py artifacts/private/context-cache-20261013/runs
python evaluations/cache-probe-20261014/boundary_model.py artifacts/private/context-cache-20261013/runs
```

第一条的 v9 数值与 `evaluations/context-cache-20261013/metrics.json` 一致（requests 382、rewrites 2、stable_miss 2,098,041、uncached 2,782,173），另外输出 `strict_rewrites` 和 `low_hits`。第二条输出与 1.2 节的表格完全一致。

## 4. Stage 1：上下文更新条目

### 4.1 `src/aster/context/prompt.py`

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| 模块常量 | 无 | 增加 `CONTEXT_NOTE_PREFIX`、`CONTEXT_SECTIONS`（2.2） |
| 新函数 | 无 | `context_notes(history, current)`（2.2 生成规则） |
| 新函数 | 无 | `is_context_note(item) -> bool`：`role == "user"`、`content` 为 `str` 且以 `CONTEXT_NOTE_PREFIX` 开头 |
| `workflow_state` | 含三个倒计时字段 | 按 2.3 改为 `budget` 字段 |
| `render_prompt(system, user, messages, groups, sections, workflow, redact_history)` | 末尾追加 `Context sections ...` 消息 | 签名改为 `render_prompt(system, user, messages, groups, notes, redact_history)`，末尾 `prompt.extend(notes)`，不再生成 `Context sections` 消息 |

### 4.2 `src/aster/context/budget.py` 的 `BudgetCalculator.calculate`

1. `sections` 只删除 `working_memory` 键，保留 `memory`、`skills`、`active_skill_bodies` 三个键。**不要**把 `workflow_state` 放进 `sections`：兜底的 `shrink_section` 会清空 `sections` 中第一个非空的键，工作流状态不能被它清掉。
2. 新增局部变量 `full_notes = False`，以及：

   ```python
   def notes():
       current = {"workflow_state": json.dumps(workflow, ensure_ascii=False), **sections}
       return context_notes([] if full_notes else history, current)

   def render():
       return render_prompt(system, user, messages, groups, notes(), redact_history)
   ```

   `history` 是 `calculate` 里已有的局部变量（传入的 `structured_history`，未经 `deepcopy`）。对空历史调用 `context_notes`，就会为每个非空部分生成条目，这就是完整模式。兜底时 `workflow` 可能被缩短，所以每次 `render()` 都要重新序列化。
3. `compacting` 为真时，在掩码循环之前执行：

   ```python
   groups[:] = [g for g in groups if not (len(g) == 1 and is_context_note(g[0]))]
   full_notes = True
   prompt = render()
   ```

   进入阶段三兜底循环前，若 `full_notes` 仍为假，同样执行上面三行。
4. 阶段二丢弃旧组的判定不变。上下文更新组含 `role == "user"`，本来就不会被丢弃；它们已在第 3 步移除。
5. 返回值：`ContextCalculation(prompt, report, history_items, retained_messages)` 中的 `history_items` 改为 `[item for group in groups for item in group] + final_notes`，`final_notes` 是生成最终 `prompt` 时用的那组条目（即 `prompt` 的最后 `len(final_notes)` 项）。`ContextBuilder.build` 的写回逻辑不变，条目由此进入持久历史。
6. `report.sections` 的键改为 `system`、`task`、`tools`、`workflow_state`、`memory`、`skills`、`active_skill_bodies`、`context_notes`（本轮新条目的 token 数）、`history`（`self.count(prompt[2:])`）、`provider_overhead`。
7. `ContextReport` 增加字段 `context_notes: int = 0`（本轮新条目数）。`policy_version` 改为 `"deterministic-v10"`。

### 4.3 回执计量（`BudgetCalculator`）

尾部不再被替换，锚要覆盖全部项：

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `observe` 设锚 | `items = prompt[:-1]`，另存 `tail` | `items = [canon(x) for x in prompt]`，删除 `tail` |
| `_first_changed_item` | `index >= len(prompt) - 1` | `index >= len(prompt)` |
| `estimate` | `added` 统计 `prompt[n:-1]`，另加尾部增量 | `added` 统计 `prompt[n:]` 中非模型项；删除尾部增量 |

`from_model` 的判定不变。

### 4.4 调用方

| 文件 | 改动 |
| --- | --- |
| `context/budget.py` `calculate`、`context/builder.py` `build` | 删除 `working_memory` 参数 |
| `core/runtime.py` `prepare_request` | 删除 `working_memory = self.memory.working_section(...)` 及传参；`context_built` 事件增加 `context_notes=report.context_notes` |
| `sessions/lifecycle.py` | 调用使用关键字参数，确认无需改动；预检写回的条目是预期行为 |
| `training/env.py` | 默认策略版本改为 `"deterministic-v10"`；`state.working_memory` 的拷贝保留 |
| `ui/commands.py` | 不变（仍然展示 `working_memory`） |

`MemoryService.working_section`、`remember_file`、`state.working_memory` 及其失效逻辑全部保留。

## 5. Stage 2：协议兼容

Responses 协议下，条目是普通 `role: user` 输入项，无需改动。另外两个协议：

- Chat（`_chat_messages`）：连续多个 user 消息合法，不需改动。
- Anthropic（`_anthropic_messages`）：相邻同角色消息会合并成同一条消息的多个块。条目跟在 `tool_result` 之后时，合并后 `tool_result` 仍在前，`history_groups` 的 `tool_result must lead` 校验通过。

**验收**：为三种协议各加一个测试，构造“工具输出后跟两个条目”的历史，调用 `request_payload` 成功；Anthropic 的结果中 `tool_result` 块排在文本块之前。

## 6. Stage 3：测试

**新建** `tests/test_context_notes.py`，至少包含：

1. **纯追加。** 用 `ContextBuilder(64000, 8192)` 模拟 30 轮，`max_turns = max_steps = 500`（排除预算分级的干扰），`memory`、`skills` 为空。每轮追加一次交换（`function_call` + 约 1,500 字符输出）并递增 `state.turn`、`state.step`，每 3 轮改一次 `state.verification`，每 5 轮改一次 `state.changed_paths`。断言：
   - 每一轮 `prompt` 都以上一轮的完整 `prompt` 为前缀（逐项 `canon` 相等），且 `report.compacted` 为假。
   - 第一轮条目数为 1（只有 `workflow_state`）；之后每轮的 `report.context_notes` 为 1 当且仅当该轮 `verification` 或 `changed_paths` 发生了变化，否则为 0。
   - 任何 `prompt` 项都不含 `"Context sections"`、`"working_memory"`、`"remaining_turns"`。
2. **倒计时分级。** `max_turns = max_steps = 40`：`turn` 从 0 到 29 时不出现 `budget`；`turn = 30` 时出现一次 `{"level": "low", ...}`；`turn >= 36` 后每轮一条 `critical`，数值正确。
3. **压缩。** 使用 v9 `tests/test_append_only_context.py` 的触发参数：压缩那一轮之后，历史中的上下文更新组恰好是“每个非空部分一个”，都位于末尾；下一轮重新满足纯追加。
4. **从历史推出生效值。** 已有 `memory` 条目后把 `memory` 置空，产生一个 `"[cleared]"` 条目；再次置空不产生条目。
5. **恢复。** 用现有检查点/恢复测试的写法：恢复后的下一次 `prepare_request` 以恢复前最后一次请求的完整 `input` 为前缀。
6. **Worker 摘录不受影响。** `tests/test_interaction_workers.py` 中 `source_excerpts` 相关测试不经修改通过。

**需要修改的既有断言**（逐条写进提交说明）：

| 文件 | 原断言 | 新断言 |
| --- | --- | --- |
| `tests/test_stable_prefix.py` | 传 `working_memory=`；断言 `prompt[-1]` 以 `Context sections` 开头、含 `"remaining_steps": 38` | 不传 `working_memory`；断言最后一项是上下文更新条目，`workflow_state` 不含倒计时 |
| `tests/test_context_state.py` | 从 `prompt[-1]` 中按 `workflow_state:\n` 切分解析 | 从最后一个 `workflow_state` 条目解析（新增测试辅助函数） |
| `tests/test_harness.py::test_read_file_updates_working_memory_and_context` | 断言工作记忆进入上下文 | 只断言 `state.working_memory` 被更新，且请求中不出现该内容 |
| `tests/test_review_regressions.py` 第 252 行 | 断言不含 `working_memory:` | 不变，应自然通过 |
| `test_stable_prefix.py`、`test_context_policy_input.py` | `deterministic-v9` | `deterministic-v10` |
| `tests/test_append_only_context.py` | v9 的“上一轮 `prompt[:-1]` 是本轮前缀” | 改为完整 `prompt` 是前缀；压缩轮次断言不变 |

**验收**：新测试通过；全量测试相对基线无新增失败；ruff 通过。

## 7. Stage 4：评测（先停下，等用户确认）

协议与 `evaluations/context-cache-20261013/PROTOCOL.md` 相同：`gpt-5.6-luna`、Responses、medium、64000 上下文、8192 输出上限、900 秒/题、4 CPU / 8 GiB、6 槽并发、空会话、单 Agent、自动记忆开启。任务是 old20，只重跑 Aster，每题一次；pi、Codex 沿用 50 题评测中的数据。保留全部失败、超时和缺失用量，不追加采样、不择优。

准备工作（不调用模型）：复制 `evaluate.py` 和 `audit.py` 到 `evaluations/context-state-20261014/`，生成新的冻结清单，写好 `PROTOCOL.md`。然后停下。

| 机制 | 指标 | v9 | 要求 |
| --- | --- | ---: | ---: |
| 纯追加 | `strict_rewrites / followups` | — | ≤ 3%，且每次都对应 `compacted` 为真 |
| 纯追加 | `prefix_drift` 为真的 `context_built` 事件 | 0 | 0 |
| 缓存 | `stable_hit_mean` | 0.783 | ≥ 0.88 |
| 缓存 | `low_hits / 稳定请求` | 120 / 358（33.5%） | ≤ 15% |
| 计量 | 回执估算 / 实际，中位数与 p95 | 1.004 / 1.025 | ≤ 1.02、≤ 1.05 |
| 总体 | `uncached` | 2,782,173 | ≤ 1,300,000 |
| 总体 | `cache_ratio` | 0.723 | ≥ 0.85 |
| 总体 | `input` | 10,043,357 | ≤ 10,043,357 |
| 质量 | 独立通过 | 13 / 20 | ≥ 15 / 20 |
| 报告 | 前台请求数、每请求平均条目数与 token 数 | 382 | 只报告 |

参照值：pi 的 `stable_hit_mean` 为 0.898、低命中占比 15.7%；Codex 分别为 0.931、11.9%（old20 口径相近）。

`boundary_model.py` 在新数据上应显示 `low:tail_straddle` 接近 0；剩余低命中应主要是供应商侧未命中，并且下一轮恢复。

## 8. 不在本方案范围：请求数差距

old20 中 Aster 有 382 个请求，pi 240 个，Codex 254 个。三者都通过的 12 题分别为 220、143、156 个。这一差距同样直接放大输入成本。离线审计（`evaluations/cache-probe-20261014/turn_audit.py`）显示，没有单一原因：

| | Aster v9 | pi |
| --- | ---: | ---: |
| 只含一次编辑调用的轮次 | 74（`patch_file`） | 52（`edit`） |
| 只含一次读文件的轮次 | 52 | 18 |
| 读文件调用 / 涉及文件数 / 读 ≥3 次的文件 | 205 / 101 / 22 | 117 / 76 / 9 |
| 完成门禁追加的用户消息 | 10 | 0 |

Aster 的 `read_file` 请求行数中位数为 71 行，pi 为 110 行，而且 Aster 读的文件更多、重复读的也更多。这是探索行为和工具粒度的问题，需要单独设计，本方案不改。

## 9. 不要改

- 系统提示词文本、工具定义及其顺序、`prompt_cache_key`（`state.session_id`）。
- v9 的压缩阈值、观测掩码、阶段二丢弃规则和说明项、写入时限长、`read_file` 字符上限。
- 回执计量中除 4.3 列出的三处以外的逻辑（作用域、干净回执判定、密文校准、加性开销）。
- `state.working_memory` 的数据结构、写入和失效逻辑；Worker `source_excerpts`；UI 展示。
- 归档格式、检查点格式、TeamBudget 公式、`_begin_handoff`。
- 不要把条目改成 diff 格式，也不要在条目里加任何非 API 字段（Responses 输入项不接受未知键）。

## 10. 参考实现与文献

调研于 2026-10-14 完成，只读克隆，代码未改动。三个参考 Agent 遵循同一条规则：**动态状态只追加，而且只在变化时追加**，没有一个每轮重建并替换尾部。

**Codex**（openai/codex `76ed3b4`，路径在 `codex-rs/` 下；`a9b0815` 结构相同）

- `core/src/session/mod.rs` `record_context_updates_and_set_reference_context_item`：没有基线（首轮或压缩后）时注入完整上下文，否则只追加与上一快照的差异（`context/world_state/mod.rs` `render_history_diff`）；没有变化就不追加。
- `environment_context`（`context/world_state/environment.rs`）只在 cwd、shell、日期、时区、沙箱变化时发出。
- `update_plan`（`tools/handlers/plan.rs`）只返回 `"Plan updated"`，计划不会被重新注入提示词。
- 预算提醒（`session/rollout_budget.rs`）只在越过阈值时追加一次，并标记已送达；没有剩余轮次计数器。
- `prompt_cache_key` 默认是会话 id（`core/src/client.rs`）。

**pi**（earendil-works/pi `b45e0eb`；`4270522` 机制相同）

- 核心不注入任何每轮动态消息，也没有内置 todo。
- 系统提示词分节构建；某节变化时，`diffSystemPromptSections`（`coding-agent/src/core/system-prompt.ts`）追加一条补丁消息。
- `prompt_cache_key = sessionId`（`ai/src/api/openai-responses.ts`）。

**Claude Code**（第三方根据泄露源码重建的 claude-code-best/claude-code `77a7934`，非官方代码；另参考官方文档 code.claude.com/docs/en/prompt-caching.md）

- `<system-reminder>` 附件随工具结果写入历史，之后原样重发（`src/query.ts`）。
- 提醒有节流：todo 提醒间隔 10 轮（`src/utils/attachments.ts` `TODO_REMINDER_CONFIG`）；日期只在跨日时追加。其注释写明这样做是为了不改动已缓存的前缀。
- 官方文档原文：“New content is appended at the end”，计划模式和技能“append their instructions as conversation messages, so the cached prefix stays intact”。

**文献**

- Lumer et al., “Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks”, arXiv:2601.06007（预印本）：提示词缓存可降低 41–80% 的成本，建议把易变内容放在末尾，避免动态工具结果进入缓存段。注意它研究的是跨会话复用，没有研究会话内的只追加。
- Lindenbauer et al., “The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management”, arXiv:2508.21433（NeurIPS 2025 DL4C workshop）：在 SWE-bench Verified 上，观测掩码的成本减半，解决率不低于 LLM 摘要。这支持 v9 的压缩方式。
- Gim et al., “Prompt Cache: Modular Attention Reuse for Low-Latency Inference”, MLSys 2024；Zheng et al., “SGLang: Efficient Execution of Structured Language Model Programs”（RadixAttention），NeurIPS 2024：前缀复用要求前缀逐 token 相同，这是本方案的机制基础。

**OpenAI 文档**（官网返回 403，以下来自镜像）

- Cookbook “Prompt Caching 201”（openai-cookbook `5bd6cb40`）：1024 token 起缓存，按 128 token 递增命中；路由依据前约 256 token 的哈希与 `prompt_cache_key`；建议把易变内容移到末尾。
- openai-python `86cae507` 的 `prompt_cache_options`（gpt-5.6 起）：“By default, OpenAI automatically chooses one implicit cache breakpoint”，也可以用 `prompt_cache_breakpoint` 显式标注断点。

“一个隐式断点”与我们观测到的“每个请求只留一个快照”一致。实测的命中粒度是 1024 token，与文档写的 128 token 不符，可能与 Codex 后端代理有关，未能确认。显式断点能否经该代理生效也未验证；它需要新增请求字段，本方案不采用，留作后续探针。

## 11. 实施记录（2026-10-15）

Stage 0–3 已完成，策略为 `deterministic-v10`。全量回归为核心 484 通过，其余 816 通过、6 失败、4 跳过；6 个失败与动手前基线一致，无新增失败。工作记忆仍供 Worker 摘录和 UI 使用，但不再渲染进模型请求。

兼容性补充包括：会话树与恢复界面区分状态条目和用户消息；旧 robustness 脚本移除已删除的参数；状态比较使用脱敏后的值。边界模型新增仅供离线分析的 `--append-only` 模式，默认 v9 结果保持不变。

Stage 4 的 20 题协议、脚本和冻结清单准备完成，尚未执行付费评测。新通过率与实际缓存收益仍待验证。实现细节、断言调整、验证记录见 [实施报告](../evaluations/context-state-20261014/REPORT.md)，运行条件见 [评测协议](../evaluations/context-state-20261014/PROTOCOL.md)。
