# 00｜先让 Aster 完成一次任务

[目录](README.md) · 下一章：[Agent 循环](01-agent-loop.md)

> 先看到“读取文件 → 收到结果 → 回答”，再拆开里面的机制。

## 这门课要解决什么问题

你可能已经会调用模型 API：输入一句话，得到一段回答。但 Coding Agent 要完成“修复代码并测试”，中间必须多次接触真实工作区。模型负责提出下一步，Harness 负责组织输入、执行允许的动作、保存结果，并决定什么时候继续或停止。

终端界面把任务交给 `Runtime`，它循环调用模型和工具。后面的章节都是在这条主线上回答一个工程问题，例如“文件刚被别人改过，还能照着旧内容写吗？”或“聊天历史太长了，如何保住调用与结果的配对？”

```text
用户任务
   ↓
Runtime：组装输入 → 模型选择动作 → 检查权限 → 执行工具
             ↑                               ↓
             └──────── 工具结果与反馈 ─────────┘
   ↓
检查完成条件 → 最终回答 + 运行记录
```

本课程围绕当前项目做源码导读。章节沿同一条运行路径展开，每个实验都直接使用 `src/aster` 的实际代码。

## 准备环境

以下命令在 **Aster 仓库根目录**执行。你已有 `aster` 环境时，只需激活；新机器第一次安装才需要创建环境。

```bash
# 仅首次安装时运行：
conda create -n aster python=3.12 -y
conda activate aster
python -m pip install -e '.[dev]'
```

`-e` 是可编辑安装：导入的是仓库里的源码，修改文件后不需要反复安装。`[dev]` 加装 pytest 等开发工具。项目依赖见 [pyproject.toml](../../pyproject.toml)：HTTPX 负责 HTTP 请求，Pydantic 校验数据，Textual 构建 TUI，tiktoken 估算输入长度。课程实验不调用模型 API，但 tiktoken 首次使用可能需要下载词表；完全断网前请准备好本地缓存。第 04 章的沙箱实验需要支持 Landlock/seccomp 的 Linux 环境。

开发时先运行 `python -m pytest -q -m smoke` 检查主要链路，再测试修改的模块。`-m core` 覆盖核心回归，省略 `-m` 会运行完整测试；三者是包含关系，无需每次都依次执行。模块选择和维护规则见[测试指南](../../tests/README.md)。

```bash
conda activate aster
python -c "import sys, aster; print(sys.executable); print(aster.__file__)"
```

第一行应指向 `aster` 环境里的 Python，第二行应指向本仓库的 `src/aster/__init__.py`。如果不对，先修正环境再继续；后续的 `python` 都指这个解释器。

## 第一个离线实验

下面不需要 API Key。我们用一个很小的模型替身：第一次提出读取请求，第二次根据真正读到的结果回答。只有模型回复被替换，运行循环、工具、权限、证据记录都使用 Aster。

把标有 `# 可运行实验` 的完整 Python 代码块保存到 `/tmp/aster-lesson.py`，然后在项目根目录运行 `python /tmp/aster-lesson.py`。后续章节沿用这个方法；其他短代码块是源码节选或数据示意，不需要单独执行。

也可以直接运行 `python docs/learning/run_examples.py 00`；小脚本会提取下面这个完整实验执行，无需手工复制。

```python
# 可运行实验
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


class ReadThenAnswer:
    def __init__(self):
        self.seen = ""

    async def complete(self, messages, tools):
        outputs = [m for m in messages if m.get("type") == "function_call_output"]
        if not outputs:
            return ModelReply(calls=[
                ToolCall(name="read_file", arguments={"path": "hello.txt"})
            ])
        # 请求里的工具结果已经渲染成纯文本：read_file 给每一行加了行号前缀。
        self.seen = outputs[-1]["output"]
        assert self.seen.startswith("1: "), self.seen
        return ModelReply(text="文件内容：" + self.seen.splitlines()[0].removeprefix("1: "))


async def main():
    with TemporaryDirectory(prefix="aster-lesson-") as directory:
        root = Path(directory)
        (root / "hello.txt").write_text("Hello, Aster!\n")
        model = ReadThenAnswer()
        runtime = Runtime(
            root,
            Settings(memory_enabled=False, auto_dream=False),
            model=model,
        )
        state = await runtime.run(TaskSpec(
            prompt="查看 hello.txt 并告诉我内容",
            require_observation=True,
        ))
        print(state.final)
        print(f"phase={state.phase}, turn={state.turn}, step={state.step}")
        print("checkpoint:", runtime.evidence.path(
            "runs", state.run_id, "checkpoint.json"
        ).is_file())
        # 同一条结果在历史里是完整的 JSON 记录；模型看到的是上面那段文本。
        record = json.loads(next(
            item["output"] for item in runtime._structured_history
            if item.get("type") == "function_call_output"
        ))
        print("模型看到:", repr(model.seen))
        print("历史里的记录字段:", sorted(record))
        assert (state.phase, state.turn, state.step) == ("completed", 2, 1)
        assert record["status"] == "success" and record["content"] == model.seen


asyncio.run(main())
```

你会看到包含 `Hello, Aster!` 的回答，以及运行状态：

```text
文件内容：Hello, Aster!
phase=completed, turn=2, step=1
checkpoint: True
模型看到: '1: Hello, Aster!'
历史里的记录字段: ['content', 'end', 'evidence_id', 'path', 'start', 'status']
```

为什么有两轮却只有一步？第一轮模型选择读文件，执行了一次工具；第二轮模型给最终回答，没有再调用工具。`turn` 数模型决策轮次，`step` 数工具执行步骤。更多细节留到下一章。

`TemporaryDirectory` 创建临时工作区，退出后自动清理，实验不会改你的业务仓库。`memory_enabled=False` 关闭跨运行记忆，方便单独观察循环；本次任务的工作记忆仍然存在。`auto_dream=False` 关闭任务结束后的长期记忆整理，本章无需它。

最后两行展示同一条结果的两种用途：模型读到带行号的文件正文；历史保存状态、证据编号和读取范围，供恢复、预算与审计使用。组装请求时，[view.py](../../src/aster/context/view.py) 的 `render_output` 把记录渲染成文本，省去 JSON 包装和转义。第 05 章会沿同一次读取展开这条路径。

这是 2026-10-19（`a774b25`）之后的做法；此前模型请求直接携带 JSON 记录。实验只读内部属性 `_structured_history` 来观察保存格式，业务集成通过 Runtime 的公开方法操作。模型替身让这条数据流可以离线复现。

## 换成真实模型

离线实验跑通以后，再按需配置真实连接。项目根目录 `.env` 使用以下字段，填写你自己的值，不把密钥提交到 Git：

```dotenv
ASTER_PROTOCOL=responses
ASTER_MODEL=gpt-5.6-luna
ASTER_BASE_URL=https://api.openai.com/v1
ASTER_API_KEY=填写自己的密钥
```

`protocol` 表示接口的消息格式（`responses`、`chat` 或 `anthropic`，区别见[第 02 章](02-model-protocols.md)；DeepSeek 官方接口和本地开源模型服务使用 `chat`），`base_url` 是接口前缀，`model` 是服务接受的模型名。兼容网关可能需要不同的地址，但不应把完整 `/responses` 路径重复写进前缀。

配置好以后，在交互终端运行 `aster` 就会打开 TUI；输入任务后才发送真实请求并产生费用。大多数参数可以省略。下面用一个新目录演示单次任务和交互启动：

```bash
mkdir -p /tmp/aster-learning-demo
printf 'Hello, Aster!\n' > /tmp/aster-learning-demo/hello.txt
aster --cwd /tmp/aster-learning-demo --read-only '查看 hello.txt 并说明内容'
# 不传任务文本，默认进入终端交互界面：
aster --cwd /tmp/aster-learning-demo
```

`.env` 默认从**启动命令时的当前目录**读取，`--cwd` 决定 Agent 的工作区，两者不同。这里从项目根启动，因而会用项目的 `.env`。也可以通过 `--env-file` 显式指定。

日常使用默认开启项目记忆和记忆演化，保存有出处的项目约定与修复经验。交互会话在边界处启动后台整理；单次 CLI 默认在打印答案后退出，待整理材料留给后续会话。

两种入口分开，是因为整理服务于未来任务，却会增加当前任务的等待和请求数。2026-10-18（`1fa30a3`、`937fb49`）将单次任务的 `oneshot_maintenance_seconds` 默认值改为 0；需要当场等待整理时，可在 TOML 中设为大于 0 的秒数，见[配置说明](../configuration.md)。实验关闭记忆是为了单独观察循环；完整机制见[第 10 章](10-layered-memory.md)和[第 11 章](11-memory-evolution.md)。

`aster '任务内容'` 执行一次任务后退出；`aster --tui`、`aster-tui` 仍可显式启动 TUI。非交互输入下需要提供任务文本，避免程序等待输入；`--resume` 与 `--rl-smoke` 保持专用执行路径。

单次 CLI 启动时会打印输入预算及来源，交互界面则在欢迎页和 `/context` 命令中显示。交互入口默认启用 Responses 流式，可用 `--no-stream` 关闭。通常让 Aster 根据模型配置自动选择预算；学习实验里的小预算只用来制造上下文压力，详见 [第 06 章](06-context-budget.md)。

## 源码怎么读

| 文件与入口 | 这一轮只需要理解的事情 |
|---|---|
| [cli.py](../../src/aster/cli.py) 的 `main` | 解析命令行，装配 Runtime |
| [schema.py](../../src/aster/schema.py) 的 `TaskSpec` / `ModelReply` | 用户任务和模型回复的数据结构 |
| [runtime.py](../../src/aster/core/runtime.py) 的 `run` / `_run` | 模型与工具为何可以反复交互 |
| [config.py](../../src/aster/config.py) 的 `load_settings` | TOML、环境变量和显式参数如何合并 |
| [view.py](../../src/aster/context/view.py) 的 `render_output` | 完整的工具结果记录，怎样变成模型看到的文本 |

不用立刻把 `runtime.py` 从头读到尾。先找到实验中调用的 `run`，只跟着它进入 `_run` 和 `turn`；暂时把上下文、权限、记忆当作已经装配好的组件。

## 练习与核对

1. 把文件内容改为 `你好，Aster！`，保留模型替身的回答逻辑，输出会怎样变化？
2. 删除 `hello.txt` 后，第一次工具结果是什么？

<details>
<summary>参考答案</summary>

1. 回答随文件内容变化，因为替身从工具结果中提取正文。
2. 工具返回以 `[error: FileNotFoundError]` 开头的文本，实验里的断言随之失败。正式模型可以根据错误换路径继续；成功读取后，任务才有完成检查所需的观察证据。

</details>

下一章将去掉外围细节，只盯着这两轮中状态如何变化。

[目录](README.md) · 下一章：[01｜Agent 循环](01-agent-loop.md)
