# 11｜记忆自进化：让使用结果影响下一次选择

[上一章：分层记忆](10-layered-memory.md) · [目录](README.md) · [下一章：计划与技能](12-plans-and-skills.md)

> 使用反馈改变知识的排序，来源和历史记录让每次更新可追溯。

本章把“自进化”拆成可以在代码中指出来的动作：从验证过的修复中提炼经验、按新证据纠正知识、记录使用反馈、调整召回排序、停用问题记录，以及用快照回滚。这里改变的是项目知识与后续选择，模型权重没有更新。

## 问题：两条建议都相关，先相信哪一条

仓库里留下了两个测试命令：`pytest` 和 `python -m pytest`。它们都包含“project tests”，都来自真实文件，关键词召回无法区分哪个更适用于当前环境。后来第一种方式执行失败，第二种方式成功。下次任务应不应该继续保持原来的召回顺序？

Aster 让记忆保留使用反馈，反馈影响下次排序；持续失败的记录则进入停用状态。排序与维护规则在 [memory.py](../../src/aster/knowledge/memory.py)，自动提炼与批量整理在 [maintenance.py](../../src/aster/knowledge/maintenance.py)。前台能及时写入，后台也能补充遗漏；沿用同一模型配置，不增加模型服务。

```mermaid
flowchart LR
    A[相关记忆被召回] --> B[记住 recalled_ids]
    B --> C[run_tests 声明实际使用的 memory_ids 并得到明确结果]
    C --> D[成功或失败计数]
    D --> E[下一次召回调整排序]
    D --> F[Dream 规则维护]
    F --> G[问题记忆停用，证据保留]
    H[手动快照] --> I[必要时回滚记录]
```

## 先分清三个开关

| `Settings` 字段 | 默认值 | 作用 |
|---|---|---|
| `memory_enabled` | `True` | 开启跨运行存储与召回，不控制短期工作记忆 |
| `memory_evolution` | `True` | 开启自动经验提炼、使用反馈、反馈排序和 Dream 维护 |
| `auto_dream` | `True` | 运行结束登记材料并做规则维护；`background_maintenance` 允许且预算足够时，再于后台提炼与整理 |

关闭 `auto_dream` 不会关闭反馈计数，仍可显式调用 `Memory.dream()`；但如果 `auto_dream=True` 而 `memory_enabled` 或 `memory_evolution` 关闭，Dream 会返回 `skipped`，不会进行维护。关闭 `memory_evolution` 后，基础记忆的来源校验、敏感信息过滤和关键词召回仍然有效；这些属于基础存储与召回规则。

这三个开关默认全部开启，用户直接启动即可使用。保留开关方便模块对照或主动关闭：保持任务和模型相同，只改变目标机制。

另有两个设置不改变记忆规则，只决定“什么时候发起整理请求”：

| `Settings` 字段 | 默认值 | 作用 |
|---|---|---|
| `background_maintenance` | `True` | 允许在会话边界于后台发起整理请求（会调用模型） |
| `oneshot_maintenance_seconds` | `0` | 单次 CLI 任务输出答案后最多等待整理多少秒（0–120）；为 0 时单次 CLI 会把 `background_maintenance` 关掉 |

它们描述的是进程怎么跑，不是任务的安全边界，所以 `Settings.identity()` 把它们排除在检查点身份之外：恢复检查点时，不会因为这两项设置不同而被拒绝。详见本章“睡眠期”一节与[配置说明](../configuration.md)。

## 从一次任务自动提炼知识

过去，Aster 主要依靠主 Agent 主动调用记忆工具；它有时会说“已记住”，实际上却没有存入。2026-10-07 起新增的 `MemoryMaintenance` 独立检查已结束任务中的材料，用户不必额外要求记忆。主 Agent 仍可立即保存或纠正知识。

流程从 `enqueue()` 开始：选取真实用户消息、最近的失败、验证通过结果及读文件证据，持久化事件引用及输入指纹。`_prepare()` 读取最多三个已结束任务和六条相关旧记忆，构成小请求。原始交互仍在 trace 中，模型只看到有上限的真实证据片段，不再额外携带助手最终答复的摘要。

如果新会话重复了已经成功整理的材料，且工作区、记忆内容版本、日期与提炼契约都没有变化，就记录 `skipped / unchanged_evidence`，不再请求模型。上一批有候选被拒绝、取消或失败时不能走这条路径。检测到“现在”“明天”等中英文相对时间表达时保留完整时间戳，减少错误地当作相同事实的风险。新约定、更正、来源变动仍会自动处理。这个检查沿用现有审计表，和用另一个模型先判断“值不值得记”不同，不额外产生分类调用。

`_request()` 使用当前模型与账户，调用一次，提出最多三项结构化操作：新增、替换/合并、停用，也允许空列表。前台可以在这期间处理下一任务。候选没有代码编辑或 Shell 权限；`_commit()` 由 Runtime 在工作区锁内核对并原子提交。

`when` 描述知识适用于什么场景，例如“工单 CSV 导出接口”；不应夹带“本轮确认”或“本轮不写代码”等对话过程。这些词可能触发临时信息过滤，导致有价值的约定未保存。提炼指令明确了这个区别，实际保存结果由提交回执确认。

| 检查 | 实际边界 |
|---|---|
| 来源 | 引用必须能在真实任务事件中找到，文件内容还需与引用时一致 |
| 修复方法 | 必须同时引用实际执行失败和时间更晚的验证通过；模型仍需判断两者是否相关 |
| 用户纠正 | 同一知识再次入库保留已有引用；替换或停用带用户来源的约定需引用时间更晚的用户消息，不能仅凭文件或推断覆盖 |
| 并发更改 | 生成期间记忆正文、适用范围或来源发生变化，则整批候选作废；查询计数变化不影响提交 |
| 保存结果 | 实际提交后才发回执；被拒绝、冲突或空操作都不会显示“已记录” |

例如支付测试发现浮点数舍入错误，并且旧实现忽略 `amount_minor`。在修复且通过测试后，可保存“该项目优先采用整数分字段，否则用 Decimal 和 ROUND_HALF_UP 转换金额”，同时保留失败、验证来源与支付记录这一适用范围。适用范围限定为该项目的支付处理。已有支付补查见[历史案例](../../evaluations/memory-knowledge-20261005/REPORT.md)，本轮自动学习验证见[固定协议](../../evaluations/memory-sleep-20261005/PROTOCOL.md)。

如果关闭 `auto_dream` 或没有可用后台模型，`Runtime._reflect_repair` 保留原有回退：真实非零执行失败后测试通过，向主 Agent 提醒一次，可保存也可跳过。正常后台路径省去这条前台提醒，避免同一经验重复提炼。这里的判断条件是 `maintenance.enabled and can_generate`，不看 `background_maintenance`，所以单次 CLI 也算正常路径：材料已经登记，由下一次交互式会话提炼，这次不再发前台提醒。自动生成 Skill 不在这一流程内。

## 后台整理何时启动

Aster 借鉴 [LightMem](https://arxiv.org/abs/2510.18866) 将及时记录与批量更新分开的思路：前台继续任务，后台处理稳定材料，提交时再协调。研究与取舍见[记忆设计](../agent-next-optimization.md)。

| 入口 | 调度方式 |
|---|---|
| 交互会话 | 任务边界尝试整理，下一任务可继续执行；退出时取消未完成请求并保留待处理引用 |
| 单次 CLI，默认 `oneshot_maintenance_seconds=0` | 登记材料、执行规则维护，打印答案后退出；后续会话处理队列 |
| 单次 CLI，显式设置 1–120 秒 | 输出答案后等待整理至指定时限 |

[cli.py](../../src/aster/cli.py) 将默认单次任务的 `background_maintenance` 设为 `False`；[MemoryMaintenance.pulse()](../../src/aster/knowledge/maintenance.py) 据此控制启动。这个区别来自 2026-10-18 的修正：第一版只取消答案后的等待，任务结束处仍已启动后台请求；`937fb49` 才将控制放到启动入口。fresh30 的分析中，后台整理平均每 30 题增加约 17.5 次请求，成果主要用于未来会话，见[v14 方案 A.1](../v14-and-agent-rl-plan.md)。

同一工作区用专用文件锁保证至多一个生成调用，提交时检查整库内容版本。正文或来源变化使候选重新准备；查询计数变化不影响提交。供应商失败或坏格式留待后续处理，不立即重复请求。

每批输入本地估算上限 4,096 token，输出上限 768；调用和已知 usage 纳入团队总预算，`memory_usage` 单列维护部分。未知用量保留未知，预算不足时延期整理。`/memory` 展示条目、来源、最近整理和用量，学习通知由实际提交事件触发。

## 反馈如何关联到实际使用的记忆

在 [Runtime._memory_text](../../src/aster/core/runtime.py) 和 `_collect_step` 对 `memory_retrieve` 的结果归集中，召回条目的 ID 会进入 `state.recalled_ids`。`recalled_ids` 是“待关联后续测试结果的条目集合”，不是“已证明有帮助的条目集合”。

Runtime 的实际反馈条件是：

```python
if (
    state.recalled_ids
    and call.name == "run_tests"
    and not outcome.data.get("idempotency_replayed")
    and (outcome.status == "success" or outcome.reason == "tests_failed")
):
    applied = set(call.arguments.get("memory_ids", [])) & set(state.recalled_ids)
    if applied:
        self.memory.record_use(applied, success=outcome.status == "success")
        state.recalled_ids = sorted(set(state.recalled_ids) - applied)
```

工具名字叫 `run_tests` 还不够，必须得到了明确的成功或 `tests_failed`。工具参数错误、审批失败、测试环境没有配置好等问题，不应该统统算成记忆的责任，因此它们不会触发这里的负反馈。重放的结果（`idempotency_replayed`：同一个操作键的重复调用直接返回旧回执，并没有真的再跑一次测试）同样不计，否则一次测试会被奖惩两次。

还要注意反馈只在 `run_tests` 上结算。经 `run_shell` 运行的测试命令从 2026-10-18（`1fec769`）起也算正式验证，但 `run_shell` 没有 `memory_ids` 参数，不会产生记忆反馈。

Agent 要通过 `run_tests.memory_ids` 明确列出实际应用并检验的记忆；省略时不更新任何条目，也不会奖励未召回的伪造 ID。这样可避免把所有召回条目一起奖惩。不过，选择条目仍由模型判断，测试也是代理信号，Runtime 没有证明某条记忆“导致”成功。

## 每个计数字段在数什么

| 字段 | 更新依据 |
|---|---|
| `support` | 合法知识再次记录，且与最近一次证据 ID 不同 |
| `contradictions` | 显式反证或丢弃 |
| `retrievals` | 选中召回，仅作观测统计 |
| `successful_uses` / `failed_uses` | 声明应用该记忆的测试得到明确结果 |
| `inactive_reason` | 停用原因；保留原始条目 |

排序使用相关性、支持证据与使用反馈。召回次数单列，避免错误知识仅因频繁出现而获得更高权重。

## 排序公式：先相关，再用反馈打破平局

`Memory.retrieve()` 的核心计算可以直接在源码里找到：

```python
confidence = (item["support"] + 1) / (item["support"] + 2)
reliability = (
    (item["successful_uses"] + 1)
    / (item["successful_uses"] + item["failed_uses"] + 2)
    if self.evolution
    else 1
)
ranked.append((overlap + confidence * reliability, item))
```

`overlap` 是查询关键词在条目正文、标签及适用条件中命中的数量；`confidence` 是支持证据项；`reliability` 是使用可靠度。分子分母中的常数用于平滑，避免新记录没有历史就出现除零或极端分数。

假设两条记录的 `support` 都是 1，关键词命中也相同。各使用一次后，成功记录的可靠度是 `2/3`，失败记录是 `1/3`，因此前者排序更高。两条记录都还存在，失败一次不会立刻停用。

这是一条固定排序公式，关键词命中数仍是主要项，反馈主要用于区分相关程度接近的记录。

## 两种维护：规则检查与模型整理

`Memory.dream()` 会扫描已有记录，按以下情况填写停用原因：

| 原因 | 含义 |
|---|---|
| `sensitive`、`transient`、`unsafe_instruction` | 内容不再满足准入条件 |
| `stale_source` | 原来源文件变化、删除或不再可安全读取 |
| `contradicted` | 有明确反证计数 |
| `repeated_failure` | 累计失败达到保守阈值 |

失败阈值的实际条件是：

```python
item["failed_uses"] >= max(2, item["successful_uses"] + 2)
```

因此零次成功、一次失败不会停用；零次成功、两次失败会停用；三次成功则至少需要五次失败。这些都是累计次数，代码不要求失败连续发生。

这里的 `Memory.dream()` 只是无需模型的规则检查，不改正文；新增 `MemoryMaintenance` 才负责知识提炼和合并。两者都保留审计记录。文件过期等安全过滤在每次召回时就会执行，不需要等 Dream 才生效。

## 快照与回滚：恢复知识条目

`snapshot()` 深拷贝当前 `lessons`，用内容哈希作为快照 ID；最多保留八份快照。`rollback(id)` 恢复这份条目列表，并写入回滚审计。深拷贝意味着后续计数变化不会悄悄修改快照。

规则 `Memory.dream()` 本身不创建快照，手工调用时可先 `snapshot()`；后台整理在变更前自动快照，并将通过准入的整批修改一次写入。保存失败会恢复原内存状态，不发布成功回执。快照也不包含工作区文件、运行状态与全部审计历史。

回滚之后仍会执行来源校验。如果磁盘文件已经从端口 8123 改为 9090，恢复旧条目不会让“8123”重新变成事实。恢复权重与恢复世界状态是两件事。

## 离线实验：同一条记忆如何降权、停用和恢复

在 `aster` 环境运行。实验通过真实读取证据保存条目，再注入反馈标签，观察排序、停用与回滚。

```python
# 可运行实验
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


async def main():
    with TemporaryDirectory(prefix="aster-evolution-lesson-") as folder:
        root = Path(folder)
        texts = [
            "Run project tests with pytest.",
            "Run project tests with python -m pytest.",
        ]
        source = root / "commands.txt"
        source.write_text("\n".join(texts), encoding="utf-8")
        runtime = Runtime(root, Settings(memory_enabled=True, memory_evolution=True, auto_dream=False))
        runtime.start(TaskSpec(prompt="Inspect project tests"))
        observed = await runtime.step(ToolCall(
            name="read_file", arguments={"path": "commands.txt"}
        ))
        await runtime.step(ToolCall(
            name="tool_load", arguments={"names": ["memory_store"]}
        ))
        ids = []
        for text in texts:
            outcome = await runtime.step(ToolCall(name="memory_store", arguments={
                "text": text, "evidence_id": observed.evidence_id,
            }))
            assert outcome.status == "success", outcome.reason
            ids.append(outcome.data["id"])

        memory = runtime.memory
        assert memory.retrieve("project tests", limit=1)[0]["id"] == ids[0]
        saved = memory.snapshot()
        memory.record_use([ids[0]], success=False)
        memory.record_use([ids[1]], success=True)
        assert memory.retrieve("project tests", limit=1)[0]["id"] == ids[1]
        print("一次反馈后：优先召回 python -m pytest")

        assert memory.dream()["deactivated"] == 0
        memory.record_use([ids[0]], success=False)
        assert memory.dream()["deactivated"] == 1
        old = next(item for item in memory.state["lessons"] if item["id"] == ids[0])
        assert old["inactive_reason"] == "repeated_failure"
        print("第二次失败后：停用 1 条，原记录仍然保留")

        assert memory.rollback(saved)
        assert len(memory.retrieve("project tests")) == 2
        assert all(item["failed_uses"] == 0 for item in memory.state["lessons"])
        print("回滚后：恢复 2 条，失败计数恢复为 0")

        source.write_text("Project commands changed.\n", encoding="utf-8")
        assert memory.rollback(saved)
        assert memory.retrieve("project tests") == []
        print("来源变化后：即使回滚，也不会召回旧事实")
        runtime.finish("lesson_finished")


asyncio.run(main())
```

预期输出：

```text
一次反馈后：优先召回 python -m pytest
第二次失败后：停用 1 条，原记录仍然保留
回滚后：恢复 2 条，失败计数恢复为 0
来源变化后：即使回滚，也不会召回旧事实
```

单次 CLI 与后续会话的调度可继续读 [test_memory_maintenance.py](../../tests/test_memory_maintenance.py) 中的 `test_one_shot_runtime_never_starts_background_consolidation` 和 `test_one_shot_cli_leaves_consolidation_for_the_next_session`：前者检查待整理材料入队，后者检查 CLI 在默认设置下结束。

## 练习与核对

1. 将实验中的 `Settings` 改成 `memory_evolution=False`。注入反馈后、期待优先召回 `ids[1]` 的排序断言还会通过吗？
2. `run_tests` 因没有可运行测试而失败，与实际测试断言失败，应不应该给相同负反馈？
3. 两次失败后直接回滚，下一次运行是否还能从 audit 中看到发生过 Dream？
4. 若后续会话也使用 `background_maintenance=False`，队列里的材料何时会被提炼？

<details>
<summary>参考答案</summary>

1. 不会。`record_use()` 不再更新计数，排序也不使用可靠度；关键词与支持证据相同时保持已有顺序。需要相应修改实验预期。
2. 不应该。前者可能是运行条件问题，不能据此判断知识错误；Runtime 只对 `success` 或 `tests_failed` 更新反馈。具体边界由 [test_memory_feedback_contract.py](../../tests/test_memory_feedback_contract.py) 检查。
3. 可以。回滚替换 `lessons`，不会删除已有审计，并且会增加 `rollback` 事件；不过审计只保留最近 256 条，不是无限历史仓库。
4. 材料保持 `pending`，等待允许后台整理的会话处理。`background_maintenance` 管的是“启动”：连显式的 `drain()` 也不会越过它，而登记（`enqueue`）与它无关，任务只要不是过短的寒暄，仍会把材料排进队列。

</details>

## 怎样评价记忆维护

[规则场景](../../datasets/memory_evolution_cases.json)与 [test_memory_evolution.py](../../tests/test_memory_evolution.py)检查准入、反馈和回滚。实际收益则固定任务与模型，观察错误复发、重复调用和交付情况；反馈由模型声明使用了哪条记忆，所以还需检查归因。

跨会话实验见[记忆改进说明](../memory-knowledge-design.md)，调度与并发检查见 [test_memory_maintenance.py](../../tests/test_memory_maintenance.py)，历史结果统一在[第 14 章](14-evaluation.md)解释。

下一章转向人明确编写的工作方法：计划记录进度，Skill 提供可按需加载的操作说明。它们与机器积累的记忆各有职责。

[上一章：分层记忆](10-layered-memory.md) · [目录](README.md) · [下一章：计划与技能](12-plans-and-skills.md)
