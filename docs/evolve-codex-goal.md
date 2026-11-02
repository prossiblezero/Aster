# 交接给 Codex：完成 Aster 自我进化实验（目标模式）

## 目标

按预注册协议 [evaluations/evolve-20261102/PROTOCOL.md](../evaluations/evolve-20261102/PROTOCOL.md) 跑完第 1 至 4 代进化。若最终基底不是种子，在冻结留出集 fresh30c 上做一次终局比较；然后写出中文报告。结论必须由数据支持，负结果照实报告。

## 完成条件

全部满足才算完成：

1. `artifacts/private/evolve-20261102/log.json` 中有第 1 至 4 代每个候选的决策（被护栏拒绝、初筛拒绝、确认拒绝、接受、确认但未选中）。
2. 若最终基底不是 `evolve-s0`：fresh30c 上种子与最终基底各跑 2 次，`evaluations/evolve-20261102/decisions/heldout-final.json` 写出主指标及置信区间。若最终基底就是种子，则不跑留出集。
3. `evaluations/evolve-20261102/REPORT.md` 写完，内容见下文“报告要求”。
4. 每代和每个阶段都已提交并打标签，没有推送。

## 当前状态（2026-11-03 交接时）

- 分支 `exp/evolve-20261101`。已有标签：`evolve-base`、`evolve-plan`、`evolve-s0`（文本层抽到 `src/aster/context/profile.json`）、`evolve-protocol`、`evolve-s1`（fresh30c 冻结，30 题）、`evolve-s2`（种子三次运行与噪声门槛）、`evolve-s3`。
- 种子在开发集 fresh30b 上跑了三次：`evo-seed-r1`、`evo-seed-r2`、`evo-seed-r3`。结果为通过 24、24、25，成本 2.24、2.28、2.24 美元等价。噪声门槛：成本比低于 0.889 才算有效（`evaluations/evolve-20261102/decisions/seed-null.json`）。
- 第 1 代已由 Claude 启动，命令是 `generation.py 1`。两个提议已生成并打了标签 `evolve-g1a`、`evolve-g1b`：
  - g1a：题目点名组件或符号时直接检索，不列整个工作区。
  - g1b：测试通过后，只有行为相关的改动才重跑测试。
  交接时两者的开发集初筛运行 `evo-g1a-r1`、`evo-g1b-r1` 只完成了 3/30 题。
- `generation.py` 在交接前改成了可续跑：已有 `report.json` 的提议直接复用，不重新采样；已准备的运行会清理掉半完成的题目、残留的网关套接字、中继容器和网络，然后续跑。这个改动和本文件已提交，标签 `evolve-handoff`。

## 执行步骤

所有命令都在 `/home/dzh/Aster` 下执行。

### 1. 收尾第 1 代

先看第 1 代是否还在运行：

```bash
ps aux | grep "[g]eneration.py 1"
```

- **仍在运行**：等它自己结束。不要并行启动别的评测，全机同时最多 6 题。结束后，`/tmp/evolve-s0/gen1.log` 的最后一行是 JSON，里面有 `next_base` 和 `next_base_runs`。
- **已不在运行，但最后一行没有 `next_base`**：说明中断了。用原命令续跑，输出改写到不会被清理的位置：

```bash
cd evaluations/evolve-20261102
/home/dzh/.conda/envs/aster/bin/python generation.py 1 --base evolve-s0 \
  --base-runs evo-seed-r1 evo-seed-r2 evo-seed-r3 \
  --seeds evo-seed-r1 evo-seed-r2 evo-seed-r3 \
  > ../../artifacts/private/evolve-20261102/gen1.out 2>&1
tail -1 ../../artifacts/private/evolve-20261102/gen1.out
```

续跑前，先用 `python3 -c "import json;print(json.load(open('artifacts/private/evolve-20261102/log.json'))[-1])"` 确认第 1 代还没有写入 log.json。如果已经写入，说明第 1 代已完成，不要重跑。

### 2. 第 2 至 4 代

每一代都用上一代输出的 `next_base` 和 `next_base_runs`。`--seeds` 始终是三次种子运行，不换成上一代：

```bash
cd evaluations/evolve-20261102
/home/dzh/.conda/envs/aster/bin/python generation.py N --base <next_base> \
  --base-runs <next_base_runs...> \
  --seeds evo-seed-r1 evo-seed-r2 evo-seed-r3 \
  > ../../artifacts/private/evolve-20261102/genN.out 2>&1
```

每代结束后提交这一代的产物，提交信息写明本代的决策：

```bash
git add evaluations/evolve-20261102/log.json evaluations/evolve-20261102/decisions \
  evaluations/harness-v12-20261017/runs/evo-gN*
git commit -m "eval: evolve generation N"
git tag evolve-genN
```

脚本中途崩溃时，用同一条命令续跑一次。若同一处再崩溃，停下来修复，写进报告，不要绕过。

### 3. 留出集终局比较（只做一次）

若第 4 代之后的基底仍是 `evolve-s0`，跳过本步，直接写报告，结论为负结果。

否则，先准备 4 次运行，并在任何模型请求之前提交清单：

```bash
PY=artifacts/private/swebench-compare-20261008/controller-env/bin/python
E=evaluations/harness-v12-20261017/evaluate.py
for r in 1 2; do
  $PY $E prepare evo-ho-seed-r$r  --ref evolve-s0    --tasks fresh30c --pool fresh30c-20261102 --concurrency 3
  $PY $E prepare evo-ho-final-r$r --ref <最终基底标签> --tasks fresh30c --pool fresh30c-20261102 --concurrency 3
done
git add evaluations/harness-v12-20261017/runs/evo-ho-*/manifest.json
git commit -m "eval: register held-out runs before running"
```

同一轮的种子与最终基底同时跑，两轮依次进行，保证配对在同一时段：

```bash
for r in 1 2; do
  ($PY $E run evo-ho-seed-r$r;  $PY $E results evo-ho-seed-r$r) &
  ($PY $E run evo-ho-final-r$r; $PY $E results evo-ho-final-r$r) &
  wait
done
```

分析时，用 `analyze.compare('heldout-final', ['evo-ho-final-r1','evo-ho-final-r2'], ['evo-ho-seed-r1','evo-ho-seed-r2'])` 求成本比、请求数比及其 90% 置信区间。另写一个小脚本 `evaluations/evolve-20261102/final.py`，补上下面两项，结果一并写入 `decisions/heldout-final.json`：

- 通过数差、正常交付且通过数差：按题配对，自助抽样 4,000 次，给出 90% 置信区间。
- 新增文字的泄漏检查：对 `git diff evolve-s0 <最终基底> -- src/aster/context/profile.json` 新增的文字，检查是否出现 fresh30c 的题号或练习名。

该结果里 `accepted` 字段对留出集没有意义，报告中不要引用。跑完留出集后，不再改动任何代码、配置或规则。

### 4. 报告要求

写 `evaluations/evolve-20261102/REPORT.md`，中文正文，标识符保留英文。

- **开头一段**：给出结论和关键数字。
- **逐代表格**：候选、改动要点、引用的证据题、护栏结果、初筛和确认的成本比与通过数、决策、提议者成本。
- **留出集表格**：种子与最终基底的通过数、正常交付且通过数、请求数、成本、带警告完成数、剔除题目及原因。再给出成本比、请求数比、通过数差及各自的置信区间，以及去掉种子最贵一题后的成本比。
- **改动审计**：逐条列出被接受的措辞改动；检查是否削弱了验证或诚实报告的要求；对照提议者引用的题号，看看对应轨迹是否真有该模式。
- **总花费**：所有评测运行加提议者，单位为美元等价；注明这是按公开价折算，不是实际账单。
- **能下和不能下的结论**：照 PROTOCOL.md 的同名一节写。只有成本比置信区间上界低于 1，才能写“显著下降”。没有第二级对照，不能写“递归自我改进”。

另外更新两处文档：

- 在 `docs/evolve-plan.md` 顶部加一行状态，链接到报告。
- 在 `docs/resume-aster.md` 末尾追加一条“自我进化”的候选简历条目，数字必须能追溯到 REPORT.md。结果为负时，写成方法加负结论，例如“预注册协议下 4 代 8 个候选均未超过噪声门槛”；不要美化。

最后提交并打标签：

```bash
git commit -m "eval: self-evolution report"
git tag evolve-s6
```

## 不许做的事

- 不改 `src/aster/` 下的任何代码；候选由 `propose.py` 自动提交在侧枝上，不移动当前分支。
- 不改 PROTOCOL.md 的规则、`analyze.py` 的门槛（`ACCEPT_QUANTILE`、`BOOTSTRAP`）、`propose.py` 的护栏和提示词、`evaluate.py`、fresh30c 题池。
- 不为了得到更好的结果而重跑任何运行或提议。供应商异常的题由剔除规则处理。
- 留出集终局比较之前，不看 fresh30c 的任何运行结果。
- 不在用户工作区直接运行 Aster 命令；提议者只在 `artifacts/private/evolve-20261102/<name>/workspace` 的副本里运行。
- 全机同时最多 6 题。`generation.py` 每代最多同时跑两次运行、每次 3 题，不要另开评测与之并行。
- 不在评测期间拉取 docker 镜像。拉取会让守护进程超时，之前发生过一次。

## Git 规则

- 只在 `exp/evolve-20261101` 上提交，每代或每阶段一次提交加一个标签。不推送，不改写历史，不执行 `reset --hard` 和 `clean`。
- 每次提交前检查暂存文件：`git diff --cached --name-only | grep -E '\.env|artifacts/private'` 必须没有输出。
- 回退命令写给用户，由用户执行，Codex 不执行：
  - 回到某代结束时：`git reset --hard evolve-genN`
  - 回到交接时：`git reset --hard evolve-s3`

## 预计花费

每次开发集运行约 2.25 美元等价，每次提议约 0.3。每代最多 4 次运行，约 9.6；第 2 至 4 代最多约 29。留出集 4 次运行约 9。全部做完不超过约 40 美元等价。如果供应商大量出错，导致某代超过一半的题被剔除，停下来报告，不要继续烧预算。
