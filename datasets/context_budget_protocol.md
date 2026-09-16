# 中档上下文回归与有限预算选择

本轮事先固定的规则见 [context_budget_protocol.json](context_budget_protocol.json)。原题、长历史与隐藏测试不变，仍属于已经查看过的开发评测集。先验证去掉单条工具正文上限后的同预算结果，再用有限候选选择预算；两件事不能合并成一个“优化收益”。

## 先跑原中档：39 次

```bash
conda run --no-capture-output -n aster python scripts/benchmark_robustness.py --real --suite context --variant balanced --repeats 3 --concurrency 8 --output artifacts/benchmark-v8-balanced
conda run --no-capture-output -n aster python scripts/compare_context_runs.py --before artifacts/benchmark-v7-context/report.json --after artifacts/benchmark-v8-balanced/report.json --output artifacts/benchmark-v8-balanced
```

`--variant balanced` 只选择原协议里的中档：总窗口 8,192，输出预留 2,048，实际输入预算 6,144。13 个场景各运行 3 次，主任务 36 次和一次性证据控制 3 次分开统计。本轮不再运行原 `full` 或 `tight` 模型条件。

报告保留原 `protocol` 和数据哈希，`effective_context_budgets` 记录实际选择的总窗口。历史的 `tool_output_tokens=1600` 只在构建生产配置时忽略，并写入 `ignored_legacy_settings`；没有改写历史协议。基础 `settings.context_tokens=24000` 仅固定参考脚本和历史配置比较，真实模型的窗口来自各条记录的 `setup.selected`。

## 三个候选：各 12 次

候选输入预算固定为 8,192／10,240／12,288，每档所有 12 道主任务各一次，不包含信息控制。沿用已有的 `--task` 参数选择这 12 道题，不新建任务或更换判分器。例如在激活 `aster` 环境后：

```python
import json
import subprocess
import sys
from pathlib import Path

protocol = json.loads(Path("datasets/context_budget_protocol.json").read_text())
cases = json.loads(Path("datasets/context_task_cases.json").read_text())["cases"]
tasks = [case["id"] for case in cases if case["category"] != "loss_control"]
for budget in protocol["selection"]["input_token_candidates"]:
    command = [
        sys.executable, "scripts/benchmark_robustness.py", "--real", "--suite", "context",
        "--input-budget", str(budget), "--repeats", "1", "--concurrency", "8",
        "--output", f"artifacts/benchmark-v8-budget-{budget}",
    ]
    for task in tasks:
        command.extend(["--task", task])
    # 任务失败会返回非零；保留结果并继续其他预先固定的候选。
    subprocess.run(command, check=False)
```

`--input-budget N` 指输入额度，执行器另外加上固定输出预留；条件名称写为 `input_N`，不冒用原 `balanced` 标签。它与 `--variant` 互斥，二者都只用于 `--suite context`。

每档必须完整保留 12 次尝试及请求。以“完整完成至少 11/12”且“平均首请求压缩至少 30%”为双门槛，选择满足门槛的最小预算。压缩率逐次计算 `1 - first_request.estimated_tokens / setup.untrimmed.estimated_tokens` 后平均，分母是本地估算的未裁剪输入；缺少真实首请求的尝试不能从计算里悄悄删掉，也不能用总计费 token 代替这个指标。

## 固定选择后再确认：39 次

例如最终选定输入 10,240（此处只是命令示例，不预先指定结果）：

```bash
conda run --no-capture-output -n aster python scripts/benchmark_robustness.py --real --suite context --input-budget 10240 --repeats 3 --concurrency 8 --output artifacts/benchmark-v8-confirmation
```

确认包含全部 13 个场景，使用新的完整批次，不合并选参阶段的成功样本。主任务至少 33/36 完整完成、平均首请求压缩至少 30%，才达到事先设定的确认门槛；信息控制另报。没有候选达标或确认未达标时，报告失败，不扩大候选范围，也不重跑失败样本来凑分。

这是同题新尝试的确认，不是未见测试集。较高预算改善完成率不能证明取消单条上限的独立收益；后一问题由前面的 6,144 输入同预算比较回答。不同输入预算不会被比较脚本伪装成同预算成本收益。
