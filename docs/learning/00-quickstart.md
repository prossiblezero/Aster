# 00｜先让 Aster 完成一次任务

[目录](README.md) · 下一章：[Agent 循环](01-agent-loop.md)

> 先看到“读取文件 → 收到结果 → 回答”，再拆开里面的机制。

## 这门课要解决什么问题

你可能已经会调用模型 API：输入一句话，得到一段回答。但 Coding Agent 要完成“修复代码并测试”，中间必须多次接触真实工作区。模型负责提出下一步，Harness 负责组织输入、执行允许的动作、保存结果，并决定什么时候继续或停止。

Aster 的入口并不神秘。终端界面把任务交给 `Runtime`，它循环调用模型和工具。后面的章节都是在这条主线上回答一个工程问题，例如“文件刚被别人改过，还能照着旧内容写吗？”或“聊天历史太长了，如何保住调用与结果的配对？”

```text
用户任务
   ↓
Runtime：组装输入 → 模型选择动作 → 检查权限 → 执行工具
             ↑                               ↓
             └──────── 工具结果与反馈 ─────────┘
   ↓
检查完成条件 → 最终回答 + 运行记录
```

本课程围绕当前项目做源码导读。章节是学习顺序，不是十几份独立的 Agent 实现；每个实验都直接使用 `src/aster` 的实际代码。

## 准备环境

以下命令在 **Aster 仓库根目录**执行。你已有 `aster` 环境时，只需激活；新机器第一次安装才需要创建环境。

```bash
# 仅首次安装时运行：
conda create -n aster python=3.12 -y
conda activate aster
python -m pip install -e '.[dev]'
```

`-e` 是可编辑安装：导入的是仓库里的源码，修改文件后不需要反复安装。`[dev]` 加装 pytest 等开发工具。项目依赖见 [pyproject.toml](../../pyproject.toml)：HTTPX 负责 HTTP 请求，Pydantic 校验数据，Textual 构建 TUI，tiktoken 估算输入长度。

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
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


class ReadThenAnswer:
    async def complete(self, messages, tools):
        outputs = [m for m in messages if m.get("type") == "function_call_output"]
        if not outputs:
            return ModelReply(calls=[
                ToolCall(name="read_file", arguments={"path": "hello.txt"})
            ])
        result = json.loads(outputs[-1]["output"])
        assert result["status"] == "success"
        return ModelReply(text="文件内容：" + result["content"].strip())


async def main():
    with TemporaryDirectory(prefix="aster-lesson-") as directory:
        root = Path(directory)
        (root / "hello.txt").write_text("Hello, Aster!\n")
        runtime = Runtime(
            root,
            Settings(memory_enabled=False, auto_dream=False),
            model=ReadThenAnswer(),
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
        assert (state.phase, state.turn, state.step) == ("completed", 2, 1)


asyncio.run(main())
```

你会看到包含 `Hello, Aster!` 的回答，以及：

```text
phase=completed, turn=2, step=1
checkpoint: True
```

为什么有两轮却只有一步？第一轮模型选择读文件，执行了一次工具；第二轮模型给最终回答，没有再调用工具。`turn` 数模型决策轮次，`step` 数工具执行步骤。更多细节留到下一章。

`TemporaryDirectory` 创建临时工作区，退出后自动清理，实验不会改你的业务仓库。`memory_enabled=False` 关闭跨运行记忆，方便单独观察循环；本次任务的工作记忆仍然存在。`auto_dream=False` 关闭任务结束后的长期记忆整理，本章无需它。

这次实验验证的是 Harness 数据流；下一步动作是提前写好的，所以它不能证明模型自主解决任务的能力。

## 换成真实模型

离线实验跑通以后，再按需配置真实连接。项目根目录 `.env` 使用以下字段，填写你自己的值，不把密钥提交到 Git：

```dotenv
ASTER_PROTOCOL=responses
ASTER_MODEL=gpt-5.6-luna
ASTER_BASE_URL=https://api.openai.com/v1
ASTER_API_KEY=填写自己的密钥
```

`protocol` 表示接口的消息格式，`base_url` 是接口前缀，`model` 是服务接受的模型名。兼容网关可能需要不同的地址，但不应把完整 `/responses` 路径重复写进前缀。

下面会发送真实请求并产生费用。准备一个新的演示目录后，再启动 CLI 或 TUI：

```bash
mkdir -p /tmp/aster-learning-demo
printf 'Hello, Aster!\n' > /tmp/aster-learning-demo/hello.txt
aster --cwd /tmp/aster-learning-demo --read-only '查看 hello.txt 并说明内容'
# 终端交互界面：
aster --tui --cwd /tmp/aster-learning-demo
```

`.env` 默认从**启动命令时的当前目录**读取，`--cwd` 决定 Agent 的工作区，两者不同。这里从项目根启动，因而会用项目的 `.env`。也可以通过 `--env-file` 显式指定。

CLI 启动时会打印输入预算及来源。通常让 Aster 根据模型配置自动选择；学习实验里的小预算只用来制造上下文压力，详见 [第 06 章](06-context-budget.md)。

## 源码怎么读

| 文件与入口 | 这一轮只需要理解的事情 |
|---|---|
| [cli.py](../../src/aster/cli.py) 的 `main` | 解析命令行，装配 Runtime |
| [schema.py](../../src/aster/schema.py) 的 `TaskSpec` / `ModelReply` | 用户任务和模型回复的数据结构 |
| [runtime.py](../../src/aster/runtime.py) 的 `run` / `_run` | 模型与工具为何可以反复交互 |
| [config.py](../../src/aster/config.py) 的 `load_settings` | TOML、环境变量和显式参数如何合并 |

不用立刻把 `runtime.py` 从头读到尾。先找到实验中调用的 `run`，只跟着它进入 `_run` 和 `turn`；暂时把上下文、权限、记忆当作已经装配好的组件。

## 自己改一次

把文件内容改为 `你好，Aster！`，但不要改模型替身的回答逻辑。输出应跟随文件变化。这说明回答来自真实工具结果，而不是代码里预先写死的答案。

再思考：如果删掉 `hello.txt`，能否仍说任务成功？当前实验会在模型替身里的断言失败。正式模型可以根据错误结果继续行动，但 Harness 不该把“读取失败”转换成虚构的文件内容。

下一章将去掉外围细节，只盯着这两轮中状态如何变化。

[目录](README.md) · 下一章：[01｜Agent 循环](01-agent-loop.md)
