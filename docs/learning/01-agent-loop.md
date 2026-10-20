# 01｜一个循环，三种操作

上一章：[跑通任务](00-quickstart.md) · [目录](README.md) · 下一章：[模型协议](02-model-protocols.md)

> 模型决定“做什么”，Runtime 负责“允许怎样做、做完如何继续”。

## 从一个具体问题开始

用户让 Agent 查看两个文件并说明关系。模型可能一次回复就提出两个 `read_file`，也可能先读一个、分析后再读另一个。Harness 接收每轮模型动作，把实际结果送回下一轮，让执行顺序随任务进展调整。

Aster 用 `run → turn → step` 表达这三个尺度。把 `run` 想成整张工单，`turn` 是一次向模型征求下一步，`step` 是执行其中一个工具调用。

```text
run(task)
  ├─ start(task)：初始化 RunState
  └─ while 未结束且未超限
       └─ turn()
            ├─ prepare_request()：整理模型输入
            ├─ model.complete()：请求一次回复
            │    └─ 回复在输出上限处被截断：不执行，追加提示后重试（每次运行最多 2 次）
            ├─ 有工具调用：_execute_batch() 逐个 step(call)，补齐结果
            └─ 只有文本：recheck_preexisting() 补跑过期的验证，再 finalize(answer) 检查能否结束
```

先沿这条主路径读代码，第四站再看批次校验、交付检查与截断恢复。

## 第一站：看循环的退出条件

[runtime.py](../../src/aster/core/runtime.py) 的 `_run` 中有下面这段实际代码：

```python
while (
    not self.state.terminal
    and self.state.step < limit
    and self.state.turn < self.state.task.max_turns
):
    await self.turn()
```

`terminal` 是 `RunState` 的属性：`completed`、`stopped`、`failed` 都算终态。模型给出最终文本不一定立刻进入终态：如果任务要求修改后测试，而测试尚未完成，`finalize` 会给反馈，允许有限次补做。

为什么同时限制 `turn` 和 `step`？一轮可能提出多次工具调用；反过来，模型也可能反复给出不合格的最终答案而没有工具调用。只限制其中一个，另一个方向就可能没有总量约束。

| 字段 | 含义 | 例子 |
|---|---|---|
| `TaskSpec.prompt` | 当前任务的要求 | “查看两个配置文件” |
| `max_steps` | 工具步骤上限 | 40 次 |
| `max_turns` | 模型决策轮次上限 | 40 轮 |
| `RunState.phase` | 当前运行状态 | `running` / `completed` |
| `stop_reason` | 为什么结束 | `verified` / `turn_limit` |
| `observed` | 是否已有成功的工作区观察 | 成功读取后为真 |
| `feedback` | 运行时给出的纠偏说明 | “还需要执行测试” |

`step` 统计进入执行阶段的调用，包括参数无效或整批预检失败的调用。重复调用编号等更早的拒绝发生在进入 `_step` 之前。

## 第二站：模型返回什么

[schema.py](../../src/aster/schema.py) 定义了统一的 `ModelReply`。本章先看三个字段：

```python
text: str = ""
calls: list[ToolCall] = Field(default_factory=list)
usage: dict[str, int] = Field(default_factory=dict)
```

`text` 是回复正文，`calls` 是本轮提出的工具动作，`usage` 是服务返回的 token 使用量。列表里的每个 `ToolCall` 有名称、参数和 `call_id`；编号把请求与对应结果连起来。

`_model_turn` 的处理顺序很关键：先保存模型原始回复，再逐个执行调用，最后为每个调用追加结果。即使中途取消，也会为未完成调用补上带 `turn_interrupted` 原因的取消结果。这个结果表示“未能确认完成”，不表示副作用被自动撤销。

默认 `read_parallelism=1`，同一轮调用按顺序执行。调大该值后，只含 `read_file`、`search`、`list_files` 的批次可并发读取，结果仍按顺序提交；带写入的批次顺序执行。Worker 的并发机制见[第 13 章](13-interaction-and-workers.md)。

## 实验：两次工具调用只占一轮模型决策

按 [第 00 章](00-quickstart.md) 的方法保存并运行。模型替身第一轮读两个文件，第二轮回答。

```python
# 可运行实验
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


class TwoReads:
    def __init__(self):
        self.requests = 0

    async def complete(self, messages, tools):
        self.requests += 1
        if self.requests == 1:
            return ModelReply(calls=[
                ToolCall(name="read_file", arguments={"path": name})
                for name in ("a.txt", "b.txt")
            ])
        outputs = [m for m in messages if m.get("type") == "function_call_output"]
        assert len(outputs) == 2
        return ModelReply(text="两个文件都已读取。")


async def main():
    with TemporaryDirectory(prefix="aster-loop-") as directory:
        root = Path(directory)
        (root / "a.txt").write_text("alpha\n")
        (root / "b.txt").write_text("beta\n")
        model = TwoReads()
        runtime = Runtime(root, Settings(memory_enabled=False, auto_dream=False), model=model)
        state = runtime.start(TaskSpec(prompt="查看两个文件", require_observation=True))
        print("start:", state.phase, state.turn, state.step)
        await runtime.turn()
        print("turn 1:", state.phase, state.turn, state.step)
        await runtime.turn()
        print("turn 2:", state.phase, state.turn, state.step)
        assert model.requests == 2
        assert (state.phase, state.turn, state.step) == ("completed", 2, 2)


asyncio.run(main())
```

预期输出：

```text
start: running 0 0
turn 1: running 1 2
turn 2: completed 2 2
```

我们刻意手动调用两次 `turn()`，让你看到中间状态。日常运行交给 `run()` 即可，不需要外部代码再写一个同样的循环。

## 第三站：工具失败会不会让整个 Agent 崩溃

工具返回统一的 `Outcome`，其中 `status` 区分 `success`、`error`、`blocked`、`timeout`、`cancelled`。例如，读取不存在的文件是工具结果，模型能据此换路径；模型服务出错则走 `ProviderError`：客户端先对暂时性故障有限重试（[第 02 章](02-model-protocols.md)），仍然失败就记录原因，以 `provider_error` 终止本次运行。只有一种服务端结果例外：回复在输出上限处被截断，Runtime 会接住它继续跑，见下一站。

因此“工具失败”和“运行失败”是两个层级。运行时允许模型从部分失败中恢复，同时用步数、停滞检测和完成条件控制无限重试。具体规则在 [第 08 章](08-hooks-and-completion.md)。

`_operation()` 还限制同一个 Runtime 同时只能有一个操作拥有执行权。它允许同一个异步任务内 `run → turn → step` 嵌套，但拒绝另一个异步任务同时修改同一状态，避免界面操作和模型循环互相覆盖。

## 第四站：主路径上的三个分支

| 情况 | Runtime 怎样继续 | 为什么 |
|---|---|---|
| 批次里有参数错误，其余调用全为只读 | 错误调用得到 `invalid_arguments`，合法调用照常执行 | 保留本轮已有的探索机会 |
| 批次里有参数错误，且含写入、进程或状态操作 | 整批在执行前返回；其余调用得到 `batch_validation_failed` | 模型能按完整的执行前状态修正请求 |
| 模型只有文本，准备交付 | `recheck_preexisting()` 补跑过期验证，再由 `finalize()` 检查条件 | 验证结果要对应最终代码 |
| 回复在输出上限处截断 | 追加提示，每次运行最多恢复 2 次 | 让模型缩短推理、给出下一步动作 |

2026-10-17（`98f0cfb`）之前，参数错误会让整批读取作废；现在预检按副作用类型区别处理。这里保证的是**参数预检阶段**的执行选择，已开始执行的多个写入仍逐个产生结果。

完成检查位于纯文本分支，因为模型此时正在宣布结束。缺少必要条件时，`finalize()` 最多要求补做 `max_final_revisions` 次（默认 2）。交付前补跑覆盖过期的既有失败，以及编辑后失效的最后一次通过命令；细节见[第 08 章](08-hooks-and-completion.md)。

输出截断恢复于 2026-10-19（`7abdba4`）加入。残缺回复不执行、不进入历史，已报告的用量照常累计；恢复占一轮 `turn`，工具步数保持不变。恢复次数耗尽后以 `provider_error` 结束，其他未完成原因由[第 02 章](02-model-protocols.md)解释。

## 练习与核对

1. 将实验改为 `await runtime.run(TaskSpec(..., max_turns=1))`，会在哪一步结束？
2. 第一次回复直接说“已经读完”，保留 `require_observation=True`，会发生什么？
3. 外部代码只调用 `runtime.step()`，会不会增加模型请求？
4. `[read_file（合法）, todo_add（合法）, search（缺少 pattern）]` 会执行哪些调用？

<details>
<summary>参考答案</summary>

1. 第一轮能读完两个文件，随后因 `turn_limit` 结束，第二轮没有机会回答。
2. 完成检查要求补做观察，下一轮继续。
3. `step()` 只执行传入的动作；请求模型由 `turn()` 负责。这也是外部策略复用 Runtime 的入口。
4. 整批在预检阶段返回。`todo_add` 的 effect 为 `state`，不满足“其余调用全为只读”。

</details>

对应回归见 [test_tool_feedback.py](../../tests/test_tool_feedback.py)、[test_provider_events.py](../../tests/test_provider_events.py) 和 [test_preexisting_failures.py](../../tests/test_preexisting_failures.py)。

上一章：[00｜跑通任务](00-quickstart.md) · [目录](README.md) · 下一章：[02｜模型协议](02-model-protocols.md)
