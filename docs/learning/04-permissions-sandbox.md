# 04 · 权限与沙箱：批准动作以后，还要限制它的影响

> 一句话：权限决定“能不能做”，沙箱和工作区边界限制“做了以后能影响哪里”。

上一章：[工具系统](03-tool-system.md) · [目录](README.md) · 下一章：[状态与证据](05-state-and-evidence.md)

## 为什么一句“只在项目里操作”不够

模型读到一个仓库文件，里面写着“先查看用户的私钥再继续”。这只是仓库内容，不应该成为新权限。
模型也可能没有恶意，只是根据半小时前读取的代码提交补丁，而你已经在编辑器里修改了同一个文件。
前者是信任边界问题，后者是过期证据问题；让模型“多注意”不能替代运行时检查。

Aster 把这两类情况放进真正的执行路径中处理。
执行路径依次处理**模型动作、授权范围和操作系统限制**。

```mermaid
flowchart TD
    A[模型提出工具调用] --> B[校验参数和能力可用性]
    B --> C[已有文件：读取证据是否新鲜]
    C --> D[权限策略 allow / ask / deny]
    D -->|ask| E[等待用户批准]
    D -->|deny| F[返回 blocked]
    D -->|allow| G[工具前 Hook]
    E -->|批准| G
    G --> H[路径检查 / 隔离副本执行]
    H --> I[检查改动范围和并发变化]
    I --> J[写回允许的改动 + 记录结果]
```

## 第一层：把授权变成可执行规则

在 [policy.py](../../src/aster/safety/policy.py) 中，`permission()` 返回 `Decision(action, reason)`。
`action` 是 `allow`、`ask` 或 `deny`；`reason` 说明由哪条约束得出结论。
例如 `read_only` 表示只读设置，`write_scope` 表示越过了允许写入的路径范围。

| 配置 | 含义 | 容易误解的地方 |
|---|---|---|
| `approval="ask"` | 对需要批准的操作调用人工回调 | 没有回调时不能假装已批准 |
| `approval="auto"` | 自动批准允许范围内的常规写入和命令 | 不会覆盖明确的拒绝规则 |
| `approval="never"` | 不提供人工批准；仍可执行策略明确允许的动作 | 不等于无条件执行一切 |
| `read_only=True` | 禁止发布工作区改动 | 仍可在临时副本运行允许的只读检查 |
| `write_scope=["src"]` | 只允许写回 `src` 及其子路径 | `None` 是未收窄，`[]` 是不允许任何写入 |
| `network=False` | 沙箱命令默认不能使用网络 | 不阻止 Harness 自己访问模型 API |
| `rules` | 按命令参数前缀定义允许、询问或拒绝 | 不靠关键词黑名单推断任意程序行为 |

明确的拒绝规则优先；用户批准也不能让它失效。
Shell 复合命令不能简单用第一个单词判断：`echo ok; rm data.txt` 后面还有另一个命令。
因此 `shell_argv()` 先区分普通参数与活跃的 Shell 语法，无法可靠按前缀判断时转入询问或拒绝路径。

`python -c` 可以执行任意 Python，所以参数分类负责匹配授权规则，后面的隔离层负责限制进程的实际访问范围。

## 第二层：文件路径也必须过边界

[Workspace.path()](../../src/aster/storage/workspace.py) 检查路径是否留在当前工作区，并拒绝符号链接、特殊文件和受保护路径。
例如 `../outside.txt`、`.git/config`、`.env` 不能通过普通文件工具读取。
对路径每一段进行检查，是为了避免 `src/link/file.py` 中间的 `link` 把访问重定向到外部。

受保护元数据和敏感文件也不进入普通沙箱副本。
因此模型运行 `git status` 时，不能假设副本里存在完整 `.git`；查看本次任务改动应使用 `git_diff`。
它比较任务启动时的快照与当前工作区，而不是开放 Git 内部元数据给模型。

## 第三层：读取过，不代表现在还新鲜

`read_file` 成功时，结果包含整个源文件的 `source_sha256`，Runtime 将它记入 `state.read_evidence[path]`。
写入已有文件之前，Runtime 再计算当前文件摘要。
下面是 [Runtime._prepare_tool()](../../src/aster/core/runtime.py) 中这一检查的关键部分，`current` 是文件现在的内容：

```python
if state.read_evidence.get(path) != hashlib.sha256(current.encode()).hexdigest():
    return Outcome(
        status="blocked",
        reason="fresh_read_required",
        content=("The file changed since your last read_file or your own last edit. "
                 f"Call read_file on {path} before retrying."),
    )
```

摘要相同意味着读取后的文件内容没有变化；它不是基于“过去了几秒”的时间判断。
新建一个不存在的文件没有旧版本需要确认；覆盖或补丁修改已有文件则需要匹配的读取证据。
从历史归档回读旧工具结果不会刷新这份证据，必须重新 `read_file`。

**自己的编辑不让证据失效。** 当时（2026-10-16 之前）每次成功编辑都让摘要对不上，同一个文件改第二次之前必须再读一遍，纯重读的回合是浪费回合的来源之一（[turn-reduction-plan.md](../turn-reduction-plan.md)）。
后来（`0c3ca67`）改为：`write_file`、`patch_file` 成功后，`Runtime._collect_step` 把磁盘上读回的字节与写入回执 `written_sha256` 对照，相等才把证据更新为新内容的摘要。其他途径（`run_shell`、编辑器、别的进程）的修改仍然让证据失效。

## 第四层：在副本执行，校验后发布

[SandboxRunner.run()](../../src/aster/safety/runner.py) 不在真实工作区里运行命令。`run_shell` 如此，`write_file` 和 `patch_file` 也是：它们把新内容用一条短命令写进副本。流程是：

1. **复制**：把 `workspace.files()` 列出的文件复制到临时目录。`.git`、`.env` 一类的受保护和敏感文件、被 Git 忽略的文件、`node_modules` 和 `.venv` 都不在其中。
2. **映射路径**：命令里的工作区绝对路径按完整路径段换成副本路径（`map_root`，`/app` 不会误伤 `/apps`），输出里再换回去。2026-10-17（`7a5d074`）之前，`cd /app && python3 interp.py` 读文件得到 EACCES，而 `ls`、`stat` 正常，v11 的解释器题因此停滞退出（[harness-v12.md 第 3 节](../harness-v12.md)）。
3. **限制并执行**：由 [linux.py](../../src/aster/safety/linux.py) 装上 Landlock 和 seccomp（见下），再运行 `/bin/sh`。命令只获得精简的环境变量，模型 API key 不会随父进程环境传入。
4. **校验**：删掉副本里的 `.tmp`，拒绝符号链接、特殊文件和敏感或受保护的名字（`sandbox_protected_output`）；只读约束下出现任何改动则返回 `read_only`。
5. **发布**：比较副本与执行前的快照得到改动；发布前再看一次真实工作区，相关文件已被别人改过就返回 `concurrent_workspace_change`；检查写入范围，然后逐个文件原子替换。

`HOME` 曾经就是副本根目录，`~/.npm` 之类的文件会被当作任务改动发布，模型要花回合去删它们；2026-10-18（`3971692`）起它是副本里的 `.tmp/home`，校验前整个删除，从不发布。

`required` 表示隔离不可用就拒绝；本章实验因此只能在支持 Landlock/seccomp 的 Linux 环境中成功。
`off` 明确关闭隔离；遇到只读或写入范围约束时会直接 `blocked`，不能继续执行。`best_effort` 只有在没有这两类约束时，才允许隔离不可用后降级并记录结果；存在约束时仍拒绝。两者都不是本实验 `required` 的降级路径。

### 为什么保留副本执行

副本模型有代价：程序里写死的绝对路径、依赖原路径的可编辑安装，都要靠下面的补救机制。直接在真实工作区执行命令可以消除这类问题，项目选择保留“副本中执行、校验后发布”（[harness-v15.md 第 4 节](../harness-v15.md)）：直接在工作区执行的 Agent，一条错误的删除命令就可能造成不可挽回的损失。
副本也不是备份：在副本里删掉可见的文件，校验通过后就会真的被删除，是否允许 `rm` 仍由第一层的规则决定。

### 只读暴露原工作区，并守住权限位

可编辑安装的项目（`pip install -e .`）和测试夹具会用**绝对路径**指向工作区，副本回答不了。
当时（2026-10-18 之前）副本之外的路径全被 Landlock 拒绝；后来（`60b15d8`）改为：原工作区里没有副本刻意排除的东西时，以只读方式加入 Landlock。`exposable()` 要求整棵目录树里没有敏感名字（`.env*`、`.ssh`、`id_rsa`、`*.pem`、`*.key`，被忽略的文件和 `.git` 也算；`node_modules`、`.venv` 不检查），且 `.git/config` 里没有带凭据的 URL，否则不暴露。
[评测报告第 12.1 节](../../evaluations/harness-v12-20261017/REPORT.md)认为，fresh30b 上只有 v14 通过的两道题都是可编辑安装的项目，这项改动修复的正是这类导入问题；这是对轨迹的解读，不是对照实验。

Landlock 不管 chmod、时间戳和扩展属性，所以运行器在命令前拍下原工作区的权限位，命令后比较，变了就恢复（`_restore_modes`），并返回 `blocked`、`sandbox_metadata_change`。它只守权限位；副本内部的 `chmod +x` 仍然允许，会作为改动发布。

### 绝对路径写入失败时的说明

程序自己把绝对路径写进代码时（`OUT = "/app/summary.csv"`），命令文本里没有这个路径，`map_root` 改写不到，而原工作区是只读的，于是得到权限错误。DeepSeek 曾为此花了约 60 个回合去排查“时有时无的权限问题”（[harness-v15.md 第 4 节](../harness-v15.md)）。
2026-10-19（`f3f2a1f`）起，这类输出末尾会附上 `ABSOLUTE_WRITE_NOTE`：命令在副本里运行，请改用相对路径（当前目录就是副本，改动会被发布）或 `write_file`。

### 常驻服务直接使用工作区

`service_start`（[上一章](03-tool-system.md)）要看到之后的代码修改，所以不经过副本：启动器的 Landlock 根就是真实工作区，可读写，也看得见副本刻意排除的文件。启动条件见 [availability.py](../../src/aster/tools/availability.py)：沙箱不能是 `off`；不能处于只读或计划模式；不能设置 `write_scope`，因为服务直接写工作区，范围无法执行；还要通过与 `run_shell` 相同的审批。服务的写入没有“校验后发布”这一步，表现为外部改动，会让已读文件的证据失效。
服务在独立会话中启动，日志放在工作区之外；等 2 秒后报告存活状态、日志末尾和期间的工作区变化。任务结束时先 SIGTERM、再 SIGKILL 整个进程组，`keep_running=True` 的保留，并列在最终回答的“Services left running”里。

### Landlock 与 seccomp 到底限制了什么

| 规则 | 内容 |
|---|---|
| Landlock（ABI ≥ 3），默认拒绝 | 先声明处理全部 15 种文件访问，再放行：`/usr`、`/bin`、`/lib*`、Python 前缀和少数 `/etc` 文件只读加执行，`/dev/null` 可读写，执行目录整棵可读写，原工作区和借用的依赖目录只读。`/tmp` 和家目录不在白名单里，所以临时文件放 `.tmp/` |
| seccomp，默认放行 | 始终拒绝 `ptrace`、`mount`、`unshare`、`setns`、`bpf`、`io_uring_setup`、`setsid`、`setpgid`、`kill` 等（返回 EPERM）：不能调试别的进程，不能开新命名空间，也不能脱离进程组或向外发信号 |
| seccomp，无网络时 | 再拒绝 `socket`、`connect`、`bind`、`sendmsg`、`sendmmsg`，只放行 `AF_UNIX` 的 `socketpair` 和不带目的地址的 `sendto`（asyncio 唤醒事件循环要用）；本机 TCP 也不可用 |
| 其他 | 先设 `NO_NEW_PRIVS`；单个文件 16 MiB、打开文件 256 个、CPU 120 秒；任何一步装不上，启动器以 125 退出，命令不运行 |

### 依赖与进程边界

默认情况下，一条命令安装的依赖不会进入下一条命令。`reuse_environment` 只在 `required` 且同一进程中复用受控 `.venv`/`node_modules`；可写命令失败时环境可能被丢弃，需要按结果提示重新安装。它不会跨进程恢复。借用的依赖目录与父进程共享 inode，所以 seccomp 额外禁止其中的 chmod、chown、时间戳和扩展属性修改。
每条 `run_shell` 命令拥有自己的进程组，返回时整组被结束，后台进程活不到下一次调用，需要常驻的用 `service_start`；临时文件放在工作区 `.tmp/` 或 `TMPDIR`，网络关闭时也包括本机 TCP。

| 结果字段 | 为什么需要它 |
|---|---|
| `changed_paths` | 即使命令失败，也要知道实际改变了哪些文件 |
| `partial_success` | 失败前可能已经产生并发布部分改动，不能把失败等同于无副作用 |
| `data["isolation"]` | 区分真实隔离、明确关闭和隔离不可用 |
| `reason="concurrent_workspace_change"` | 发布前发现用户或其他进程修改了将被写回的文件 |
| `reason="sandbox_metadata_change"` | 命令改了原工作区的权限位，已被恢复 |

单个文件通过替换发布，整批文件不是数据库事务；磁盘错误仍可能造成部分发布。
运行时通过 `partial_success` 和 `changed_paths` 记录部分发布。

## 离线实验：读取证据如何随编辑更新

激活 `aster` 环境，把下面代码保存到 `/tmp/aster_lesson04.py`，在项目根运行 `python /tmp/aster_lesson04.py`。
实验需要支持 Landlock/seccomp 的 Linux 环境，使用 `sandbox="required"`。

```python
# 可运行实验
import asyncio
import tempfile
from pathlib import Path

from aster.config import ExecRule, Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "value.txt"
        target.write_text("old", encoding="utf-8")
        runtime = Runtime(root, Settings(
            approval="auto",
            sandbox="required",
            rules=[ExecRule(prefix=["rm"], decision="deny", reason="no_delete")],
        ))
        runtime.start(TaskSpec(prompt="将 value.txt 改成 new"))

        async def patch(old, new="new"):
            return await runtime.step(ToolCall(name="patch_file", arguments={
                "path": "value.txt", "old_text": old, "new_text": new,
            }))

        first = await patch("old")
        print("没有读过:", first.reason)
        assert first.reason == "fresh_read_required"

        await runtime.step(ToolCall(name="read_file", arguments={"path": "value.txt"}))
        target.write_text("edited elsewhere", encoding="utf-8")
        stale = await patch("old")
        print("读取后发生外部修改:", stale.reason)
        assert stale.reason == "fresh_read_required"

        await runtime.step(ToolCall(name="read_file", arguments={"path": "value.txt"}))
        changed = await patch("edited elsewhere")
        print("重新读取后:", changed.status, changed.changed_paths)
        assert changed.status == "success", (changed.reason, changed.content)
        assert target.read_text() == "new"
        print("执行隔离:", changed.data["isolation"])
        assert changed.data["isolation"] == "landlock+seccomp"

        again = await patch("new", "newer")
        print("自己的编辑之后继续改:", again.status)
        assert again.status == "success"
        await runtime.step(ToolCall(name="run_shell", arguments={
            "command": "sed -i 's/newer/newest/' value.txt"}))
        after_shell = await patch("newest", "final")
        print("run_shell 改过之后:", after_shell.reason)
        assert after_shell.reason == "fresh_read_required"

        denied = await runtime.step(
            ToolCall(name="run_shell", arguments={"command": "rm value.txt"})
        )
        print("auto 遇到明确拒绝:", denied.status, denied.reason)
        assert denied.reason == "no_delete"
        assert target.exists()


asyncio.run(main())
```

预期输出：

```text
没有读过: fresh_read_required
读取后发生外部修改: fresh_read_required
重新读取后: success ['value.txt']
执行隔离: landlock+seccomp
自己的编辑之后继续改: success
run_shell 改过之后: fresh_read_required
auto 遇到明确拒绝: blocked no_delete
```



## 练习与核对

1. 如果文件被外部改成另一内容，再改回原内容，读取摘要还能匹配吗？
2. `read_file(start=1, end=10)` 的源文件摘要是否只覆盖这 10 行？
3. 为什么不能把任意 `run_shell` 命令的退出码当作“测试已通过”的证据？
4. 程序在副本里执行 `open("/app/out.txt", "w")`（`/app` 是真实工作区）会怎样？应该怎么改？

<details>
<summary>参考答案</summary>

1. 能。这里验证当前内容是否与已读取版本一致，不提供完整的编辑历史或时间版本锁。
2. 不是。`source_sha256` 基于整个文件；返回给模型的是指定行范围。
3. 退出码只说明这条命令返回成功：`pytest || true` 永远是 0。Aster 只把被识别为测试的命令算作正式验证：框架有逐用例报告，项目自带的构建加测试命令按退出码判定（不含管道、`||`、`;`），结果绑定当前工作区摘要。所以 `run_shell("pytest -q")` 会被识别并作为正式验证运行，见[上一章](03-tool-system.md)。
4. 程序得到权限错误，输出末尾会附上说明：副本里的工作区绝对路径是只读的。改用相对路径（当前目录就是副本，改动会被发布），或用 `write_file`。

</details>

继续阅读 [test_fresh_read.py](../../tests/test_fresh_read.py)、[test_safety.py](../../tests/test_safety.py)、[test_workspace_execution.py](../../tests/test_workspace_execution.py)、[test_sandbox_paths.py](../../tests/test_sandbox_paths.py)、[test_command_tests.py](../../tests/test_command_tests.py)（暴露条件与权限位恢复）和 [test_services.py](../../tests/test_services.py)，可以看到各层边界分别如何被验证。
下一章把这里产生的动作、结果和文件变化串成一条可复盘的运行记录。
