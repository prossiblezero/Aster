# 14｜从“能跑”到“知道改动是否有用”

上一章：[交互与 Worker](13-interaction-and-workers.md) · [目录](README.md) · 下一章：[环境接口（选读）](15-rl-interface.md)

> 先确定要回答的问题，再固定任务、对照与指标；沿逐题轨迹解释结果。

## 一个看起来成功的实验

Agent 压缩了长历史，最后说“完成了”。输入长度说明请求变轻，完成状态说明运行达到自身条件，独立测试才回答候选代码是否满足题目。要衡量任务成本，还需统计整个过程的全部请求。

Aster 因此保留几层互相衔接的证据：

| 层次 | 回答的问题 | 入口 |
|---|---|---|
| 工程契约 | 参数、权限、配对、恢复怎样工作？ | `tests/` 与参考动作回放 |
| Runtime 完成条件 | 观察、编辑、验证和产物要求是否满足？ | `Verifier.final()` |
| 独立判分 | 候选代码是否通过题目测试？ | 评分副本与外部评分器 |
| 效果对照 | 改动对通过率、正常交付、请求、成本有什么影响？ | [evaluate.py](../../evaluations/harness-v12-20261017/evaluate.py) 与逐题分析 |

## 题目、执行器和评分器分开

```text
datasets：任务 + 初始文件
       ↓
干净工作区 → Runtime + 策略 → 候选代码
                                ↓
评分副本加入隐藏测试 → 独立判分 → 逐题结果、轨迹、checkpoint、patch.diff
```

`fixtures` 提供起点，`checks` 在执行结束后加入评分副本。“隐藏”表示本次执行没有把验收测试交给 Agent。任务的来源、许可证、拆分用途记录在 [datasets/SOURCES.md](../../datasets/SOURCES.md)。

以 [debug-and-fix](../../datasets/benchmark_tasks.json) 为例，任务要求修复 `src/handler.py` 的循环并运行测试。[评分器](../../datasets/checks/debug-and-fix.py)分别检查最后一个元素和空输入；[benchmark_support.py](../../scripts/benchmark_support.py) 的 `prepare`、`run_task`、`external_check` 将任务、执行和评分串起来。

评分前先确认原始实现的目标用例失败、已有行为通过，再确认参考修复能通过。评分器读取结构化结果，检查用例数量、名称、跳过与采集错误。候选 Python 与测试共用解释器等隔离条件记录在[评分说明](../grader-boundary.md)。

## 实验一：运行一条参考回放

下面从任务文件读取参考动作，使用真实 Runtime 和 Linux 沙箱。需要支持 Landlock/seccomp 的环境，无模型请求。

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

预期末行是 `参考动作通过: True 模型用量: None`；前面有 `baseline_target_fails=True`、`regression_passes=True` 和 `PASS`。`None` 表示该记录没有模型用量，参考回放通过实际执行链检查机制。

要保留工件供阅读，可运行：

```bash
python scripts/benchmark.py --task debug-and-fix --env-file /dev/null --output artifacts/learning-evaluation
```

## 10 月中旬形成的评测方法

### 成本：先统一分量，再比较总量

[harness-v12 报告](../../evaluations/harness-v12-20261017/REPORT.md)沿用以下公开价格权重：

```text
加权成本（美元等价）=
(未缓存输入 × 1.25 + 缓存输入 × 0.125 + 输出 × 10) / 1e6
```

这是比较用的价格折算，与实际账单分列。失败请求和后台维护也计入；缺失 usage 保留为未知，主口径汇总已知费用。若做外推，须另列假设与未补齐值：例如报告第 15.2 节的 GPT 表显式按已知请求均值补齐，同时保留原始费用。跨模型比较沿同一权重观察计算负担，各家实际价格另算。

缓存比例使用“缓存输入 / 总输入”。比例下降时，输入总量和总费用仍可能下降，所以同时看未缓存输入、缓存输入、输出与请求数。任务耗时合计和并行运行的墙钟时间也分别报告。

### 判分环境：先用参考补丁确认环境

2026-10-17，两道 requests 题连官方参考补丁都失败。检查发现主机网络会应答 `10.255.255.1:80`，而测试需要它超时。tarpit 判分网络把该地址放在链路内、保持无人应答；24 个历史补丁复判与历史结果 **24/24 一致**，见[报告第 5 节](../../evaluations/harness-v12-20261017/REPORT.md)。

这个顺序很实用：先查评分器与环境，再解释模型能力变化。预检中确定的环境规则统一应用到各组，历史补丁复判记录单列保存。

### 留出集：抽题、预检、冻结，再运行

[fresh30 的 prepare.py](../../evaluations/fresh30-20261018/prepare.py)与 [fresh30b 的 prepare.py](../../evaluations/fresh30b-20261018/prepare.py)采用预定种子的哈希排序，排除已经用过的任务，按题型抽样。环境预检完成后，提交题目清单、运行 manifest 并打冻结标签，再评测候选版本。

v13 在 fresh30 评测后读取轨迹来找问题，这套题随后成为开发集；v14 使用新的 fresh30b 留出。fresh30b 的轨迹也参与后续改进，因此 v14.2、v15 与完成检查对照按开发性质报告。留出身份取决于使用历史，文件名中的 `fresh` 不会自动延续它，见[报告第 9、11、12 节](../../evaluations/harness-v12-20261017/REPORT.md)。

### 配对：同一题、同一时段、同一条件

尽量同时运行新旧版本，固定模型参数、题目顺序、工具权限、预算、容器资源与评分器。每题有重复运行时先取均值，再计算版本之间的题目差值；[analyze.py](../../evaluations/fresh30-20261018/analyze.py)对这些题目配对重抽样 10,000 次，形成 bootstrap 95% 置信区间。

重抽样单位是题目，保留一题在各版本中的对应关系。单次 20–30 题约有 ±2 题波动；区间跨 0 时，应报告观察方向及不确定范围。离群任务保留在主表，再列排除后的敏感性分析，便于看出结论是否由一题主导。

### 第二个模型：检查结论能否跨模型成立

2026-10-19 起，评测同时使用 GPT Responses（流式）与 DeepSeek 官方 Chat Completions（非流式）。两种协议、推理习惯和调用路径给同一设计不同压力，也暴露了 Chat 历史工具名解析与输出截断处理的问题，见 [harness-v15.md](../harness-v15.md)。

[报告第 15.4 节](../../evaluations/harness-v12-20261017/REPORT.md)合并两个模型、两套题的 98 个题目样本：

| v15 相对 v14.2 | 估计变化 | 95% 置信区间 |
|---|---:|---|
| 每请求输入 | −9.8% | [−14.2%, −5.1%] |
| 独立通过，每 30 题 | +0.36 | [−1.17, +1.99] |
| 请求数 | +1.9% | [−11.3%, +18.0%] |
| 总成本 | −1.9% | [−16.6%, +17.0%] |

每请求输入在四个分组都下降，合并后区间也在 0 以下；质量与总成本没有测出显著变化。输入格式变短的效果较稳定，整项任务的请求数还受少数长任务和解题路线影响。

### 消融：围绕具体疑问改一个因素

| 疑问 | 对照 | 结果与用途 |
|---|---|---|
| 纯文本视图是否让 BigCodeBench 变差？ | v14.2、v15、工具结果改回 JSON、状态便笺改回 JSON | DeepSeek 的 20 题各跑 3 次，通过数为 34/60、37/60、38/60、38/60；未形成新视图导致退化的证据，见[第 15.1 节](../../evaluations/harness-v12-20261017/REPORT.md) |
| 完成检查是否改善交付？ | 严格评测设置、产品默认、关闭 | 共同可比 14 题均过 13 题；关闭组比默认请求多 13.3%、已知费用高 6.5%，见[10-20 报告](../../evaluations/acceptance-ablation-20261020/REPORT.md) |

完成检查的补做次数分别为 3、4、0，没有救回任务；关闭组除去 8 个供应商中断后，22 题里有 19 题仍为 `verified`，其余 3 题带警告交付，见[终态数据](../../evaluations/acceptance-ablation-20261020/comparison.json)。产品保留默认检查，提供低成本的完成反馈和状态标注；本轮强模型没有测出质量收益，弱模型上的作用另行验证。

两种“严格”也要分清：产品 `completion=strict` 按提示推断要求；评测的严格组强制所有题修改并测试。本轮默认仅 18/30 题要求测试，其余 10 道 BigCodeBench、2 道终端题按提示处理。严格组来自历史运行，因此报告按共同题目排除供应商故障，并保留全表供复核。

### “便宜的失败”：请求更少可能只是更早停下

[报告第 15.3 节](../../evaluations/harness-v12-20261017/REPORT.md)中，DeepSeek 的旧版有 4 题耗尽输出预算后直接停止；新版可提示恢复，因而会继续探索，有时成功、有时仍失败。额外请求应与交付变化一同解释。

完成检查对照也有类似现象：关闭组全表减少的费用约 61.4% 来自 `terminal-write-compressor` 提前遇到供应商错误。把全表、共同可比题目、终止原因与未知用量放在一起，才能区分真实降本与提前失败。输出截断属于本轮要评价的运行行为，v15 报告保留它；传输与服务故障按事先规定共同剔除。

## 早期实验怎样接上这些方法

下表保留截至 10 月 9 日的历史结果。各行对应当时冻结的代码与协议，后续修复和补查在原报告中单列。

| 时间与来源 | 当时的问题 | 观察与后续取舍 |
|---|---|---|
| [09-20 会话操作](../../evaluations/session-ux-20260920/PROTOCOL.md)、[09-21 编码回归](../../evaluations/session-quality-20260921/REPORT.md) | 分支、恢复与多会话是否减少操作？ | 操作路径与模型代码质量分别测量；编码回归使用同模型、同预算的 5 道题 |
| [10-05 知识记忆](../../evaluations/memory-knowledge-20261005/REPORT.md) | 明确要求学习后，下一任务能否复用？ | 严格复用 4/10 → 7/10；学习 token +43.8%，复用 −11.7%，合计 +1.7% |
| [10-06 自动学习](../../evaluations/memory-sleep-20261005/REPORT.md) | 省去“请记住”后能否保存？ | 正常交付且通过 4/10 → 7/10；14/14 回执对应实际条目，已报告总 token +12.6% |
| [10-07 记忆降耗](../../evaluations/memory-cost-20261007/REPORT.md) | 减少重复整理和工具往返 | 正常交付均为 9/10，行为断言 30/30 → 27/30；已报告 token −39.9%，三个用量分量均下降 |
| [10-09 SWE-bench 十题复核](../../evaluations/swebench-review-20261009/REPORT.md) | 完成后自动追加补丁复核是否有用？ | 通过数仍为 2/10，六次复核后记录 931,400 个已报告 token，最终移除自动追加复核 |

记忆实验均为定向开发题，前两组还存在用量缺失。学习、复用和全部尝试分开统计，便于看清新增机制的收益与开销。自动学习报告中的 CLI 最多等待 15 秒属于当时行为；2026-10-18 起单次 CLI 默认延期整理，见[第 11 章](11-memory-evolution.md)。

## 实验二：算一次 Pass@k

每题做 `n` 次、成功 `c` 次，从中随机选 `k` 次，至少成功一次的估计为：

```text
Pass@k = 1 - C(n-c, k) / C(n, k)
```

先按题计算，再对任务求平均。下面使用教学数字：

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

预期输出为 `Pass@1 = 66.67%`、`Pass@3 = 100.00%`。每题只有一次样本时，项目函数对 Pass@3 返回 `None`；报告需要与实际采样次数一致。

## 练习与核对

1. 设计“测试后再改文件”的机制检查，应观察哪些状态？
2. 新版总费用更低，但有几题因服务故障提前结束，应如何比较？
3. fresh30b 的失败轨迹已用于改进代码，下次评测应怎样标注？
4. 每请求输入显著下降、总成本区间跨 0，应写什么结论？

<details>
<summary>参考答案</summary>

1. 先确认测试通过，再修改文件，检查验证失效及交付前补跑；判断状态转换而非只匹配一句错误文字。
2. 保留全表，按预定故障规则共同选出可比题目，另列终止原因和未知用量。
3. 将其标为开发集；需要确认泛化时，重新抽题、预检并冻结留出集。
4. 本轮每请求输入稳定减少，总成本与质量未测出显著变化，并附相应区间和报告。

</details>

完整索引见 [evaluations/README.md](../../evaluations/README.md)，任务组织见[评测说明](../benchmark.md)。从结论沿任务、条件、轨迹、评分规则回到原始记录，是阅读所有报告的共同方法。

上一章：[13｜交互与 Worker](13-interaction-and-workers.md) · [目录](README.md) · 下一章：[15｜环境接口（选读）](15-rl-interface.md)
