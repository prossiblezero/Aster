# 14｜从“能跑”到“证明有用”

上一章：[交互与 Worker](13-interaction-and-workers.md) · [目录](README.md) · 下一章：[环境接口（选读）](15-rl-interface.md)

> 先定义要证明的结论，再决定任务、对照和指标。

## 一个看起来成功的实验

你给 Agent 很长的历史，它压缩了 60%，最后说“完成了”。这能证明什么？只知道输入变短、循环结束，还不知道代码是否正确。再补隐藏测试，才能判断压缩后任务是否完成。若要说“压缩节省了任务成本”，还需要同题对照，以及全部请求的真实用量。

这正是 Aster 把评测分层的原因。不同层回答不同问题，不应把几百条规则检查、少量真实模型任务和若干 token 配置加成一个总成功率。

| 评测层 | 想回答的问题 | 主要入口 |
|---|---|---|
| 基础回归 | 工具、状态、权限、恢复能按约定工作吗？ | `benchmark.py` 的参考动作模式、`tests/` |
| 上下文治理 | 输入是否合法、是否变短、压缩后还能做对吗？ | 上下文压力配置及真实代码专项 |
| 记忆收益 | 后续任务是否少读文件？旧事实失效时会不会误用？ | 跨运行任务、记忆演化场景 |
| 恢复正确性 | 重复、震荡、中断、漂移是否按边界处理？ | Runtime 机制场景、故障注入任务 |

任务完成、效率、鲁棒性、安全是观察这些层的评价维度。例如权限拒绝可以是安全成功，但绝不能不分任务要求就算成任务完成。

## 题目、执行器和评分器分开

```text
datasets/benchmark_tasks.json：题目与条件
           ↓
fixtures/ → 复制成干净工作区 → Runtime + 策略
                                     ↓ 候选代码
checks/   → 评分副本加入隐藏测试 → 独立验收
                                     ↓
结果目录：逐次记录 + 轨迹 + 检查点 + patch.diff
```

`fixtures` 是初始仓库。`checks` 是评测器持有的测试文件，在模型结束后才加入评分副本。这里“隐藏”指没有交给本次执行中的模型，不表示开源测试从未出现在基础模型的预训练数据中。

具体读一条 [debug-and-fix 任务](../../datasets/benchmark_tasks.json)：它要求根据错误日志修复 `src/handler.py` 的循环，并执行测试。以下是原始数据节选，省略参考动作：

```json
{
  "id": "debug-and-fix",
  "suite": "repository",
  "split": "evaluation",
  "fixture": "fixtures/long_session_debug_fix",
  "max_steps": 32,
  "max_turns": 24,
  "require_edit": true,
  "require_verification": true,
  "checks": {
    "phase": "completed",
    "verifier": "checks/debug-and-fix.py"
  }
}
```

| 字段 | 意思 |
|---|---|
| `id` | 稳定题号，跨批次用它对齐 |
| `suite` | 题型，例如仓库修改或函数实现 |
| `split` | 数据用途；当前这些题属于评测 |
| `fixture` | 在 `datasets/` 下查找的初始文件目录 |
| `checks.phase` | 运行必须达到的状态 |
| `checks.verifier` | 结束后运行的独立代码测试 |
| `actions` | 参考动作，只给确定性回放使用 |

先读 [checks/debug-and-fix.py](../../datasets/checks/debug-and-fix.py)：`test_fix` 检查最后一个元素是否被计入，`test_regression` 检查空输入仍返回 0。然后读 [benchmark_support.py](../../scripts/benchmark_support.py) 的 `prepare`、`run_task`、`external_check`，跟踪一条题目如何变成一次实验。

## 三种“通过”分别是什么

第一种是 **Runtime 完成检查**：有没有看过工作区、发生修改、执行有效测试、产生要求的文件。这是约束执行过程，不能单独证明代码满足所有功能要求。

第二种是 **独立代码验收**：结束后在评分副本运行隐藏测试，检查候选代码。在 Aster 的主代码任务中，它与运行条件一起决定是否完整完成。代码通过但工具预算耗尽、没有正常收尾，报告仍保留这个差异。

第三种是 **机制符合预期**：例如关闭恢复时应停止、开启时应继续。这两组都符合各自预期，不等于两组都完成了同一个目标。因此机制报告把 `expected`（是否按约定工作）与 `goal`（目标是否达成）分开。

独立评分读取结构化 JUnit 测试结果，核对测试数量、名称、跳过、采集错误等；不是在 stdout 里搜一个 `passed` 就算通过。但候选 Python 代码与测试仍共享解释器，这不构成对任意恶意代码的绝对防作弊隔离。详见 [评分边界](../grader-boundary.md)。

## 实验一：真正运行一条参考回放

本实验不调用真实模型。它从数据文件加载已有参考动作，走实际 Runtime 和 Linux 沙箱，保留一次临时报告，并检查原始版本失败、参考修复通过。需在项目根目录、Conda `aster` 中执行。

```python
# 可运行实验
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

repo = Path.cwd()
assert (repo / "scripts/benchmark.py").is_file(), "请从 Aster 根目录运行"
with TemporaryDirectory(prefix="aster-eval-lesson-") as directory:
    result = subprocess.run(
        [sys.executable, "scripts/benchmark.py", "--task", "debug-and-fix",
         "--env-file", "/dev/null", "--output", directory],
        check=True, text=True, capture_output=True, timeout=180,
    )
    print(result.stdout)
    report = json.loads((Path(directory) / "report.json").read_text())
    row = report["rows"][0]
    baseline = report["baseline_checks"][0]
    assert baseline["fix_fails_before"] and baseline["regression_passes_before"]
    assert report["mode"] == "scripted" and row["passed"]
    print("参考动作通过:", row["passed"], "模型用量:", row["total_tokens"])
```

预期看到 `baseline_target_fails=True`、`regression_passes=True`、`PASS`，最后模型用量为 0。它证明参考动作能穿过真实执行链并产出正确候选，不能写成模型自主修复成功率。

需要保留文件慢慢看时，单独执行下面这条命令，打开报告中的运行目录：

```bash
python scripts/benchmark.py --task debug-and-fix --env-file /dev/null --output artifacts/learning-evaluation
```

如果 Linux 隔离能力不可用，应检查 [第 04 章](04-permissions-sandbox.md) 的前提；不应为了得到绿色结果而绕过当前评测的 `sandbox="required"`。

## 实验二：自己算一次 Pass@k

每道题采样 3 次，不等于“36 次通过数就是 Pass@3”。设某题做了 `n` 次，其中 `c` 次成功，从这些尝试里随机选 `k` 次，至少一次成功的估计是：

```text
Pass@k = 1 - C(n-c, k) / C(n, k)
```

`C(a,b)` 是从 a 个元素中选 b 个的组合数；失败次数不够选 k 个时，全失败概率为 0。对每题算完再平均，才是任务集合的指标。

```python
# 可运行实验
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from benchmark import pass_at_k

# 教学数字，不是 Aster 的实测成绩。
rows = [
    {"task_id": task, "passed": passed}
    for task, attempts in {
        "easy": [True, True, True],
        "hard": [False, False, True],
    }.items()
    for passed in attempts
]
print(f"Pass@1 = {pass_at_k(rows, 1):.2%}")
print(f"Pass@3 = {pass_at_k(rows, 3):.2%}")
assert abs(pass_at_k(rows, 1) - 2 / 3) < 1e-9
assert pass_at_k(rows, 3) == 1
```

输出是 `66.67%` 和 `100.00%`。这说明“每题三次内至少成功一次”可以很好看，但单次可靠性仍有不足。若每题只有一次样本，项目函数对 Pass@3 返回 `None`，不会编一个三次成功率。

## 如何设计一个可信的模块对照

以记忆为例，先让两组经历相同的读取与记录阶段，再在新的 Runtime 中回答同一问题；开关只控制长期记忆召回，源文件仍在。分别统计答案是否正确、重复读取次数和调用成本。不能只给开启组看过资料，再说关闭组输掉证明设计好。

以恢复为例，先固定重复调用或交替震荡的故障前缀，再比较后续是否达到同一目标。规则回放能证明运行时识别、阻止、纠偏是否触发；真实模型接手实验才能观察它是否会利用反馈。若真实模型两组都成功，应如实写“当前任务未显示增益”，继续按机制作用范围解释，不能硬造提升。

以压缩为例，要同时看下面四件事：

| 指标 | 计算或判断方式 |
|---|---|
| 压缩前后输入长度 | 对同一初始任务请求、相同协议和工具定义计数 |
| 平均压缩率 | 每次算 `1 - after/before`，再平均 |
| 压缩后的完成率 | 模型实际执行代码题，通过独立验收及运行条件 |
| 完整任务成本 | 汇总所有模型请求的 usage、工具调用和耗时 |

首请求压缩 60% 不能直接写成总计费节省 60%。提前失败的任务往往更便宜，所以成功率和全部尝试成本必须同时保留；比较成功任务成本时，最好列出共同成功样本数量，防止两组恰好留下了不同难度的题。

## 当前报告从哪里读

任务分类和数量以 [datasets/README.md](../../datasets/README.md) 为入口，方法总览见 [评测说明](../benchmark.md)，当前上下文结论见 [上下文结果](../context-results.md)。最新上下文报告同时保留：主任务确认、同预算退步、一次性证据控制失败、代码版本和全部尝试清单。

这些题包含公开 HumanEval 的函数改造及自建仓库任务。仓库验收借鉴 SWE-bench 的思路，不代表跑过 SWE-bench 官方全集；选定的 HumanEval 子集也不能写成完整榜单成绩。来源与许可证见 [SOURCES.md](../../datasets/SOURCES.md)。

## 最后的练习

为“修改后旧测试结论失效”设计一条机制场景。先明确目标，再列操作：跑测试成功 → 改文件 → 尝试结束。预期应是旧验证失效、要求重新测试，而不是仅检查返回值含有一个错误单词。场景参数放在 `datasets/`；执行器读取场景，复用现有 Runtime。

再想一个反例：如果关闭与开启 Hooks 两组都阻止重复 `call_id`，能把差异归给 Hooks 吗？不能。重复编号还有不可关闭的协议校验，需要隔离你真正消融的那项机制，见 [第 08 章](08-hooks-and-completion.md)。

到这里已经走完 Harness 主线。后续写简历时，每个量化结论都应能指回“任务定义 → 实验条件 → 原始运行 → 评分规则 → 汇总”。下一章只解释已有环境接口，为以后讨论训练留出接口认知。

上一章：[13｜交互与 Worker](13-interaction-and-workers.md) · [目录](README.md) · 下一章：[15｜环境接口（选读）](15-rl-interface.md)
