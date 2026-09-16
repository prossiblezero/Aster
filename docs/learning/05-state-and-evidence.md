# 05｜状态、历史和证据分别放在哪里

上一章：[权限与沙箱](04-permissions-sandbox.md) · [目录](README.md) · 下一章：[上下文预算](06-context-budget.md)

> 当前该怎么继续，看状态；过去发生过什么，看证据；模型这轮能看到什么，看请求。

## 为什么有聊天记录还不够

假设 Agent 说“测试已经通过”，随后又修改了代码。聊天里那句“通过”仍然存在，但它已经不能证明当前版本可用。类似地，模型读过一个文件，不代表它下一轮仍看到完整正文，也不代表断电后能恢复执行。

因此 Aster 没有把所有问题都塞进一个 `messages` 列表。它分别保存当前状态、原生模型交互和运行证据。它们职责不同，可以从同一次工具调用派生，但不能互相替代。

```text
一个 ToolCall
    ↓
Runtime.step() → Outcome
    ├─ 更新 RunState：改了哪些文件、验证是否有效
    ├─ 追加原生交互：调用编号 ↔ 结果
    ├─ 更新 working_memory：近期可用事实
    └─ 写 trace / checkpoint：供审计和恢复

下一轮 prepare_request()
    └─ 从状态与历史构造受预算限制的模型输入
```

## 第一张表：内存中的几类对象

| 对象 | 保存什么 | 谁主要使用 |
|---|---|---|
| `TaskSpec` | 本次要求、上限与完成条件 | Runtime、Verifier |
| `RunState` | 步数、阶段、待办、验证状态等 | 所有运行时组件 |
| `RunState.messages` | 工具观察及附加消息，带 `important` / `native` 标记 | 状态记录、上下文构造 |
| `Runtime._structured_history` | 工具调用、对应结果及原生模型字段 | 协议适配、上下文、恢复 |
| `RunState.working_memory` | 本次运行整理出的文件事实与近期结果 | 提示词中的工作记忆区 |
| `Outcome` | 一次工具动作的真实结果 | 状态更新、记录和模型反馈 |

`native=True` 表示这条观察已有原生交互形式，上下文不应再把它重复塞一份。`important` 表示压缩时需要特殊保留，但不代表其中的文字获得更高执行权限。

`RunState` 的多数记录继承自 `Record`。在 [schema.py](../../src/aster/schema.py) 中可看到：

```python
model_config = ConfigDict(extra="forbid", validate_assignment=True)
```

这让未知字段和不合规的赋值尽早报错。不过，它不是递归监视器：原地修改普通列表、字典的内容，仍需要业务代码维护其约束，不能只靠一句 Pydantic 配置。

## 第二张表：一次工具结果怎么读

| `Outcome` 字段 | 含义 | 不该如何理解 |
|---|---|---|
| `status` | 成功、错误、拒绝、超时或取消 | `success` 不代表整项任务正确 |
| `content` | 供模型阅读的结果正文 | 不应拿自由文本代替结构化状态 |
| `reason` | 可归类的原因代码 | 空字符串不是异常 |
| `changed_paths` | 本次动作导致变化的文件 | 不等于改动一定符合用户要求 |
| `partial_success` | 部分动作已经完成、整体仍有问题 | 失败不一定代表零副作用 |
| `evidence_id` | 可追溯到证据事件的编号 | 不能随便编造一个字符串充证据 |
| `data` | 给运行时使用的结构化信息 | 不必原样全部塞进模型输入 |

比如 `read_file` 的 `data.source_sha256` 是内容摘要，用来判断下一次编辑是否仍基于读过的版本。它不是密码学签名，也不是模型对文件的“理解分数”。

## 第三张表：磁盘上的记录

`EvidenceStore` 默认把记录放到**目标工作区**的 `.aster/`，不是固定写到 Aster 安装目录：

```text
工作区/.aster/
  runs/<run_id>/
    trace.jsonl           # 按事件追加，一行一个 JSON
    checkpoint.json       # 最新可恢复状态
    report.json           # 结束时的摘要
    requests/*.json       # 实际构造请求的审计副本
    observations/*.json   # 可按需回读的工具观察归档
```

某些目录要等对应动作发生后才出现。主评测脚本还会把运行记录和候选代码复制到指定结果目录，并生成 `patch.diff`；不要以为每次普通 Runtime 运行都会自动在 `.aster/runs` 下生成这份评测 diff。

`trace` 是事件日志，适合回答“先发生了什么”；`checkpoint` 是状态快照，适合恢复；`report` 是运行总结，适合汇总；`requests` 回答“这一轮送给模型的输入长什么样”。它们的日志副本经过脱敏，并非包含认证信息的网络抓包。

## 代码走读：追加日志与原子快照

[evidence.py](../../src/aster/evidence.py) 的 `emit()` 给事件加入 `seq`、`previous` 和 `hash`。`seq` 是序号，`previous` 指向上一个事件摘要，`hash` 是当前记录的内容摘要。

检查点使用 `atomic_json()`：先写同目录临时文件，刷新内容，再 `os.replace()` 替换目标。这样读者不会看到只写到一半的 JSON。但它只保证这个文件的替换边界，不等于工具对整个工作区做了数据库事务。

## 实验：沿一次读取找到状态与证据

本实验不调用模型，直接执行一个工具步骤，然后显式结束任务。

```python
# 可运行实验
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.workspace import digest


async def main():
    with TemporaryDirectory(prefix="aster-evidence-") as directory:
        root = Path(directory)
        (root / "settings.txt").write_text("timeout = 30\n")
        runtime = Runtime(root, Settings(memory_enabled=False, auto_dream=False))
        state = runtime.start(TaskSpec(prompt="查看 settings.txt", require_observation=True))
        outcome = await runtime.step(ToolCall(
            name="read_file", arguments={"path": "settings.txt"}
        ))
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


asyncio.run(main())
```

预期包括 `证据事件: tool_decision read_file`、`状态: completed 观察: True`，工作记忆包含 `settings.txt`，检查点步骤为 1。哈希链检查在实验中全部通过。

注意这里 `turn` 仍是 0：我们没有向模型请求决策。相同的工具执行与证据路径可以被不同调用方复用。

## 练习与边界

1. 为什么保存 `Outcome.evidence_id` 之外，还要保存文件哈希？前者证明“这次运行发生了一个事件”，后者判断“现在的文件是否还是那个版本”。
2. 哈希链是不是不可篡改审计？不是。它能帮助检查顺序和内容变化，但能改整个日志的人也可能重算整条链；当前没有外部签名或可信时间戳服务。
3. 为什么不把检查点原样当下一轮 prompt？检查点包含恢复和审计字段，还有完整历史；模型输入应经过筛选、去重和预算处理。

下一章就进入这个筛选过程：保留恢复所需状态，同时只给模型当前预算内的输入。

上一章：[04｜权限与沙箱](04-permissions-sandbox.md) · [目录](README.md) · 下一章：[06｜上下文预算](06-context-budget.md)
