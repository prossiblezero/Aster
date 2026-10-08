# 01｜一个循环，三种操作

上一章：[跑通任务](00-quickstart.md) · [目录](README.md) · 下一章：[模型协议](02-model-protocols.md)

> 模型决定“做什么”，Runtime 负责“允许怎样做、做完如何继续”。

## 从一个具体问题开始

用户让 Agent 查看两个文件并说明关系。模型可能一次回复就提出两个 `read_file`，也可能先读一个、分析后再读另一个。Harness 不能预先规定固定的“读两次再回答”流水线；它需要接收模型动作，并把真实执行结果送回下一轮。

Aster 用 `run → turn → step` 表达这三个尺度。把 `run` 想成整张工单，`turn` 是一次向模型征求下一步，`step` 是执行其中一个工具调用。

```text
run(task)
  ├─ start(task)：初始化 RunState
  └─ while 未结束且未超限
       └─ turn()
            ├─ prepare_request()：整理模型输入
            ├─ model.complete()：请求一次回复
            ├─ 有工具调用：逐个 step(call)，补齐结果
            └─ 只有文本：finalize(answer)，检查能否结束
```

这张图省略了异常、取消与落盘分支。它用于建立阅读路径，不是另一份实现。

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

`step` 更精确地说是进入工具步骤的计数，不是成功次数。被执行阶段拒绝的调用也可能消耗步骤；重复调用编号等前置拒绝则可能在进入 `_step` 前返回。读成本报告时不要把 `step` 自动解释成“成功使用了这么多个工具”。

## 第二站：模型返回什么

[schema.py](../../src/aster/schema.py) 定义了统一的 `ModelReply`。本章先看三个字段：

```python
text: str = ""
calls: list[ToolCall] = Field(default_factory=list)
usage: dict[str, int] = Field(default_factory=dict)
```

`text` 是回复正文，`calls` 是本轮提出的工具动作，`usage` 是服务返回的 token 使用量。列表里的每个 `ToolCall` 有名称、参数和 `call_id`；编号把请求与对应结果连起来。

`_model_turn` 的处理顺序很关键：先保存模型原始回复，再逐个执行调用，最后为每个调用追加结果。即使中途取消，也会为未完成调用补上带 `turn_interrupted` 原因的取消结果。这个结果表示“未能确认完成”，不表示副作用被自动撤销。

默认配置按顺序执行一轮里的工具调用，但这不是无条件的串行保证：当 `read_parallelism > 1` 时，`_execute_batch` 可并行执行只含 `read_file`、`search`、`list_files` 的纯读取批次；预检、结果提交仍按顺序进行。混合批次以及默认 `read_parallelism=1` 仍串行。Worker 是另一条显式的并发机制，见 [第 13 章](13-interaction-and-workers.md)。

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

工具返回统一的 `Outcome`，其中 `status` 区分 `success`、`error`、`blocked`、`timeout`、`cancelled`。例如，读取不存在的文件是工具结果，模型能据此换路径；模型服务连接失败则走 `ProviderError`，记录原因并终止本次运行。

因此“工具失败”和“运行失败”是两个层级。运行时允许模型从部分失败中恢复，同时用步数、停滞检测和完成条件控制无限重试。具体规则在 [第 08 章](08-hooks-and-completion.md)。

`_operation()` 还限制同一个 Runtime 同时只能有一个操作拥有执行权。它允许同一个异步任务内 `run → turn → step` 嵌套，但拒绝另一个异步任务同时修改同一状态，避免界面操作和模型循环互相覆盖。

## 练习与参考思路

1. 在实验任务上设置 `max_turns=1`，再用 `await runtime.run()` 驱动。第一轮读取可以完成，但第二轮回答没有机会执行；结束原因应是 `turn_limit`。
2. 把第一轮回复改成纯文本，并保留 `require_observation=True`。为什么不能立即成功？因为“说读过”不等于记录里真的读过，完成检查会要求补做观察。
3. 直接调用 `await runtime.step(ToolCall(...))` 会不会自动请求模型？不会。它执行一个外部提供的动作，是后续 RL 环境共用运行时的基础；外部策略需要自行管理决策轮次。

读完应能回答：**一次 run 有多轮 turn，一轮 turn 可以有多个 step；步骤成功、运行结束、任务正确是三个不同的判断。**

上一章：[00｜跑通任务](00-quickstart.md) · [目录](README.md) · 下一章：[02｜模型协议](02-model-protocols.md)
