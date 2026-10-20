# 05｜状态、历史和证据分别放在哪里

上一章：[权限与沙箱](04-permissions-sandbox.md) · [目录](README.md) · 下一章：[上下文预算](06-context-budget.md)

> 当前该怎么继续，看状态；过去发生过什么，看证据；模型这轮能看到什么，看请求。

## 为什么有聊天记录还不够

Agent 测试通过后又改了代码，聊天里的“通过”还在，验证对应的版本却已经变了。恢复执行、核对改动与组装下一轮请求各需一份合适的数据，因此 Aster 把当前状态、历史记录和模型视图分开。

```text
ToolCall → Runtime.step() → Outcome
  ├─ trace：保存完整动作与结果
  ├─ RunState：更新文件变化、验证状态、工作记忆
  ├─ 历史：追加调用与结果的 JSON 记录
  └─ checkpoint：保存当前状态与历史

prepare_request() → 从历史渲染纯文本 → 计数与压缩 → 模型请求
```

## 内存中的状态与历史

| 对象 | 保存什么 | 用在哪里 |
|---|---|---|
| `TaskSpec` | 目标、预算与完成条件 | Runtime、Verifier |
| `RunState` | 步数、阶段、待办、验证和已读文件摘要 | 决定下一步执行 |
| `RunState.messages` | 工具观察及附加消息，带 `important` / `native` 标记 | 状态记录与上下文构造 |
| `Runtime._structured_history` | 原生模型回复、工具结果记录、追加的状态便笺 | 协议适配、预算、恢复、训练 |
| `RunState.working_memory` | 已读文件及近期搜索、测试的前 500 字符摘录 | Worker 交接和界面展示 |
| `Outcome` | 一次动作的结果 | 状态更新、日志与反馈 |

`native=True` 表示消息已有对应的原生历史，组装请求时跳过重复副本；`important` 用于压缩时保留普通消息。记录类型继承 [schema.py](../../src/aster/schema.py) 的 `Record`，用 `extra="forbid"`、`validate_assignment=True` 检查字段与赋值；列表、字典的原地更新由业务代码维护约束。

工作记忆早期也进入每轮提示。2026-10-15 的 v10 将它移出模型请求，保留交接和界面用途：原文已有历史记录，重复且不断变化的摘录还会改变缓存前缀，见[状态便笺方案](../context-state-plan.md)。

## 同一条结果，三种表示

2026-10-19（`a774b25`）起，记录与请求明确分开。以读取 `settings.txt` 为例：

| 位置 | 内容 | 目的 |
|---|---|---|
| trace 的完整 `Outcome` | 正文、状态、完整证据 ID、文件 `source_sha256` 等 | 审计、核对来源与文件版本 |
| 历史及 checkpoint 的结果记录 | JSON：正文、状态、12 位证据前缀、读取范围、归档引用等 | 恢复与上下文加工 |
| 请求中的模型视图 | `1: timeout = 30` | 让模型直接阅读文件内容 |

这里的“完整记录”相对于模型视图而言。trace 保存完整 `Outcome`；历史使用适合上下文管理的结果记录，例如文件摘要保留在 trace 和 `state.read_evidence` 中。`Runtime._append_output` 写历史，[render_output](../../src/aster/context/view.py) 在组装请求时生成模型视图。

早期直接发送 JSON 记录，代码与测试输出需要转义，记账字段又随历史反复发送。[v15 设计说明](../harness-v15.md)统计，fresh30b 中每条工具结果的包装平均约 111 token；分离后，模型只读下一步行动需要的内容。

| 结果信息 | 模型看到的形式 |
|---|---|
| 错误、阻止、超时等状态 | 首行 `[error: tests_failed]` 等；成功时通常省略状态 |
| 正文 | 原样文本，保留代码换行 |
| 改动路径 | `[changed: a.py]`；写入、补丁结果已说明改动时省略 |
| 翻页与捕获截断 | `[more from line N]`、`next_offset`、已捕获字节数 |
| 压缩后的摘录 | 页脚给 `observation_read` 回读入口 |
| 空结果 | `[no matches]`、`[no files]`、`[empty file]` 等 |
| 证据引用 | `memory_store` 已加载时，读取、命令与测试结果显示 `[evidence …]` |

`render_output` 是确定性纯函数：相同记录、工具名和引用开关产生相同文本。两次压缩之间，旧请求项能保持字节一致，便于供应商复用前缀缓存；加载工具或压缩历史时，前缀按新内容重建。

v15 在两个模型、两套题上的合并结果是每请求输入下降 9.8%，95% 置信区间为 [−14.2%, −5.1%]；总成本变化未达到统计显著，见[评测报告第 15.4 节](../../evaluations/harness-v12-20261017/REPORT.md)。第 14 章解释如何把输入长度、请求数和成本放在一起判断。

## 磁盘上的记录

`EvidenceStore` 默认在目标工作区的 `.aster/` 下保存：

```text
runs/<run_id>/
  trace.jsonl           # 按事件追加，含完整 Outcome
  checkpoint.json       # 当前 RunState 与历史
  report.json           # 结束时的摘要
  requests/*.json       # 实际构造请求的脱敏审计副本
  observations/*.json   # 可回读的历史工具正文
```

`context_built` 事件中的 `request_artifact` 与 `request_sha256` 把请求工件和 trace 对上。模型诊断还记录尝试次数、故障类别及未知 usage，方便核算请求成本。评测脚本另行生成候选代码和 `patch.diff`，见第 14 章。

[evidence.py](../../src/aster/storage/evidence.py) 的 `emit()` 用 `seq`、`previous`、`hash` 串起事件顺序，检查日志内容的一致性。`atomic_json()` 先写同目录临时文件，再用 `os.replace()` 发布完整检查点；落盘节奏见[第 09 章](09-checkpoint-resume.md)。

## 实验：沿一次读取找到状态与证据

本实验不调用模型，直接执行一个工具步骤，组装（但不发送）一次请求，然后显式结束任务。

```python
# 可运行实验
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aster.config import Settings
from aster.context.view import render_output
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.storage.workspace import digest


def result_of(items):
    return next(item["output"] for item in items if item.get("type") == "function_call_output")


async def main():
    with TemporaryDirectory(prefix="aster-evidence-") as directory:
        root = Path(directory)
        (root / "settings.txt").write_text("timeout = 30\n")
        runtime = Runtime(root, Settings(memory_enabled=False, auto_dream=False))
        state = runtime.start(TaskSpec(prompt="查看 settings.txt", require_observation=True))
        outcome = await runtime.step(ToolCall(
            name="read_file", arguments={"path": "settings.txt"}
        ))
        prompt, _, _ = runtime.prepare_request()  # 只组装请求，不调用模型
        runtime.finalize("已读取配置。")

        events = runtime.evidence.events(state.run_id)
        previous = ""
        for index, event in enumerate(events, 1):
            body = {key: value for key, value in event.items() if key != "hash"}
            assert event["seq"] == index and event["previous"] == previous
            assert event["hash"] == digest(json.dumps(body, sort_keys=True, ensure_ascii=False))
            previous = event["hash"]
        decision = next(event for event in events if event["hash"] == outcome.evidence_id)
        print("证据事件:", decision["event"], decision["tool"])
        print("状态:", state.phase, "观察:", state.observed)
        print("工作记忆:", sorted(state.working_memory))
        saved = json.loads(runtime.evidence.path(
            "runs", state.run_id, "checkpoint.json"
        ).read_text())
        print("检查点步骤:", saved["state"]["step"])
        assert saved["state"]["phase"] == "completed"

        # 同一次读取的三种形态：trace 里是完整 Outcome，检查点里是 JSON 记录，请求里是纯文本。
        traced = next(e for e in events if e["event"] == "tool_result")["outcome"]
        stored = result_of(saved["structured_history"])
        record = json.loads(stored)
        shown = result_of(prompt)
        assert len(traced["evidence_id"]) == 64 and "source_sha256" in traced["data"]
        assert record["evidence_id"] == traced["evidence_id"][:12] and "source_sha256" not in record
        assert shown == render_output(stored, "read_file") == "1: timeout = 30"
        # 渲染是纯函数；证据引用只在 memory_store 已加载时才显示。
        assert render_output(stored, "read_file", references=True) == f"[evidence {record['evidence_id']}]\n{shown}"
        print("历史记录字段:", list(record))
        print("模型看到的文本:", repr(shown))


asyncio.run(main())
```

预期输出：

```text
证据事件: tool_decision read_file
状态: completed 观察: True
工作记忆: ['settings.txt']
检查点步骤: 1
历史记录字段: ['status', 'evidence_id', 'content', 'path', 'start', 'end']
模型看到的文本: '1: timeout = 30'
```

哈希链检查在实验中全部通过。后半段的断言展示了分离：检查点里的记录只有 `evidence_id` 的 12 位前缀和读取范围，没有 `source_sha256`；完整的 64 位编号和摘要在 trace 的 `Outcome` 里；请求里只剩 `1: timeout = 30` 这一行。

注意这里 `turn` 仍是 0：我们没有向模型请求决策。相同的工具执行与证据路径可以被不同调用方复用。

## 练习与核对

1. 证据编号和文件摘要分别回答什么问题？
2. 为什么 checkpoint 需要结果记录，模型请求却使用纯文本？
3. 若在每条工具结果中加入当前时间，会怎样影响缓存？
4. 哈希链可以检查什么，它的信任前提是什么？

<details>
<summary>参考答案</summary>

1. 编号定位一次执行事件；文件摘要用于比较当前内容与当时读取的版本。
2. checkpoint 要恢复结构、游标和引用；模型主要需要正文与下一步操作所需的少量字段。
3. 历史项每次渲染都会变化，缓存前缀和回执计量的锚都会失效。
4. 它检查事件顺序和内容一致性；信任前提是整条日志未被一并替换，当前实现没有外部签名。

</details>

下一章沿 `prepare_request()` 看预算如何决定本轮输入。

上一章：[04｜权限与沙箱](04-permissions-sandbox.md) · [目录](README.md) · 下一章：[06｜上下文预算](06-context-budget.md)
