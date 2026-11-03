# 交接给 Codex：自我进化第二轮（DeepSeek 执行、扩大可改范围、同时段配对）

## 目标

用第一轮的工具搭第二轮实验，回答一个问题：换成一个行为上浪费更多的执行模型、把控制反馈文本也开放修改、并消除跨时段混杂之后，Aster 自己改写自己的循环能否在冻结留出集上显著减少请求数。结论如实报告，负结果也照写。

## 为什么要改：第一轮失败的原因与依据

第一轮（[REPORT.md](../evaluations/evolve-20261102/REPORT.md)）4 代 8 个候选没有一个被接受。原因有三条：

1. **条件对进化最不利。** 第一轮用的是强模型（GPT）和人工调过的框架，只开放措辞，目标是成本。文献里的规律正好相反：
   - [Rethinking the Evaluation of Harness Evolution](https://arxiv.org/abs/2607.12227)：从精调框架出发，强模型上的进化赢不了简单的重复采样。
   - [Harness Updating Is Not Harness Benefit](https://arxiv.org/abs/2605.30621)：中档模型获益最大，提议者用较弱的模型也能产出相当的改动。
   - [Where Does Harness-Optimization Value Live?](https://arxiv.org/abs/2609.02889)：价值集中在反思与控制这一块。
   - [Self-Harness](https://arxiv.org/abs/2606.09498)：针对模型特有的失败模式做改动，在中档开源模型上收益最大。
2. **跨时段混杂。** 种子先跑，候选后跑。期间供应商的缓存命中率下降，候选的单次请求成本因此高出 6% 到 9%，这与措辞无关。
3. **提议者探索面窄。** 每份提议都引用同两三道题，第 4 代还没写出任何改动就用完了步数。

已有数据显示，DeepSeek（`deepseek-flash`）有改进空间：

- **请求数**：在 fresh30b 的仓库类题上，它的请求数是 GPT 的 2 到 4 倍，例如 matplotlib-25960 是 75 对 19，sphinx-10323 是 70 对 36。
- **通过率**：在 old20 的 12 道仓库题上，两次运行都只通过 8 道。

## 完成条件

全部满足才算完成：

1. 第二轮代码与工具全部提交，每个阶段打了标签。
2. 开发集噪声分布、每一代每个候选的配对决策都写入 `evaluations/evolve-v2-20261104/decisions/`。
3. 若有候选被接受：在 fresh30c 上做完终局比较，写出 `decisions/heldout-final.json`。若没有：按协议跳过。
4. `evaluations/evolve-v2-20261104/REPORT.md` 写完。只有在下文“简历”一节的条件满足时，才修改 `docs/resume-aster.md`。

## 固定条件

- **分支**：从 `exp/evolve-20261101` 的当前 HEAD 新建 `exp/evolve-v2-20261104`，先打基线标签 `evolve2-base`。
- **新文件位置**：公开部分放 `evaluations/evolve-v2-20261104/`，私有产物放 `artifacts/private/evolve-v2-20261104/`。先复制第一轮的 `common.py`、`traces.py`、`analyze.py`、`propose.py`、`generation.py` 再修改；第一轮目录下的文件一律不改，保证第一轮可复现。
- **执行模型**：`deepseek-flash`，走 chat 协议。评测准备参数为 `--model deepseek-flash --protocol chat --key-env DEEPSEEKAPI_KEY --base-url-env DEEPSEEKBASE_URL`。其余参数与第一轮相同：每题 900 秒，`--acceptance benchmark`。
- **开发集**：general-50 题池中 old20 和 new30 的 20 道仓库类题，用题号列表传给 `--tasks`，题池为默认的 `general-coding-50-20261012`。题号可用下面的命令取出：

  ```bash
  python3 -c "import json;print(' '.join(t['id'] for t in json.load(open('artifacts/private/general-coding-50-20261012/tasks.json')) if t['kind']=='repository' and 'pool' not in t))"
  ```

  这 20 题过去都用于开发，只作搜索集。
- **留出集**：`fresh30c-20261102` 的全部 30 题。第一轮没有在它上面运行过任何模型，至今仍未被看过。
- **提议者**：Aster 自己，运行基底候选导出的代码，模型也是 `deepseek-flash`，所以执行者和改进者是同一个。需要一份 DeepSeek 的 TOML 模型档案，写法见 `docs/configuration.md` 的 `[profiles.*]`：`api_key_env = "DEEPSEEKAPI_KEY"`，`base_url` 取 `.env` 中 `DEEPSEEKBASE_URL` 的值，`protocol = "chat"`。档案放在 `artifacts/private/evolve-v2-20261104/deepseek.toml`，**不提交**。
- **并发**：全机同时最多 6 题。每代的种子、候选 A、候选 B 三次运行同时跑，每次 `--concurrency 2`。

## 阶段

阶段按顺序进行：上一阶段的验收不过，不进下一阶段。

### 阶段 1：把控制反馈文本放进 profile

本阶段要改的代码：

1. **`src/aster/context/profile.json`**：新增一个键 `control`。它是一个对象，固定包含下面 6 个子键，初始值逐字取自现有代码：

   | 子键 | 现有代码位置 | 原文 |
   | --- | --- | --- |
   | `missing_prefix` | `src/aster/core/recovery.py` 的 `completion_feedback` | `"Completion requirements missing: "` |
   | `rerun_failures` | 同上，`pending` 分支中测试列表之后的固定文字 | `". Rerun those cases after fixing them; passing a different selection does not resolve them. Report remaining failures honestly"` |
   | `preexisting_failures` | 同上，`identical` 分支中测试列表之后、条件后缀之前的固定文字 | `". If the task requires one of them, fix it; otherwise finish again and list them as pre-existing and unverified"` |
   | `run_final_tests` | 同上，`elif` 分支 | `"run the relevant tests on the final code (run_tests, or a test command in run_shell; other shell checks are not verification)"` |
   | `pending_failing` | `src/aster/context/prompt.py` 的 `workflow_state`，`before_final_answer` 第一条中计数之后的文字 | `" failing test(s) from run_tests must pass or be shown to fail identically on the original files"` |
   | `pending_passing` | 同上，第二条 | `"passing tests on the final code (run_tests or a test command in run_shell)"` |

   动态部分（测试名、命令、计数、`" after making the requested change"` 这个条件后缀）仍由代码拼接，不进 profile。
2. **`src/aster/context/profile.py`**：`validate` 要求 `control` 恰好包含这 6 个键，每个值都是非空字符串，长度不超过 600 字符。错误信息沿用现有写法。
3. **`recovery.py` 和 `prompt.py`**：改成从 `PROFILE["control"]` 读取上述文字。
4. **测试**：在 `tests/test_profile.py` 中补上 `control` 缺键、多键、空串、超长四种拒绝情况的用例。

验收：

- 对 `system_prompt`、`definitions()`、`completion_feedback`（覆盖 pending、identical、两者同时、无失败只缺测试这几种状态）和 `workflow_state`（覆盖两种 pending），做改动前后逐字节一致的对比脚本。脚本输出必须全部相同。
- `python -m pytest -q` 全部通过。

提交，标签 `evolve2-s1`。这个提交就是第二轮的种子。

### 阶段 2：工具改造与冒烟测试

在 `evaluations/evolve-v2-20261104/` 的副本上修改：

- **`traces.py`**：支持 chat 协议的转录。助手消息带 `tool_calls`，工具结果是 `role=tool` 并带 `tool_call_id`，从中还原工具名。可以对照现有的 Responses 分支，也可以参考 `src/aster/context/prompt.py` 的 `render_prompt` 怎样把三种协议的调用名对应起来。
- **`propose.py`**：
  - 提议者通过 `--config <deepseek.toml> --profile <名>` 使用 DeepSeek。
  - 提示词里说明新开放的 `control` 文本。
  - 把“至少两道题”改成“每个改动至少引用三道不同题目的证据”。
  - 加一条：先在 scores.md 里挑出请求数最多的 5 道题，逐一查看对应轨迹，然后才能动手修改。
  - `--max-turns` 默认值改为 60。
  - 泄漏检查的题名清单改为开发集 20 题。
  - 候选测试加上 `tests/test_profile.py` 中与 `control` 有关的用例。
- **`analyze.py`**：新增同时段配对比较。
  - 每个候选只与和它同时跑的那次种子运行配对。
  - 主指标是请求数比（候选总请求 / 配对种子总请求，只算共同保留的题），按题自助抽样 4,000 次，给出 90% 置信区间。
  - 成本比、通过数差、正常交付且通过数差一并报告。
  - 剔除规则与第一轮相同。
- **`generation.py`**：每代先产生 A、B 两个提议，B 能看到 A 的提议。然后同时跑三次运行：本代种子（`evolve2-s1`）、A、B。随后按“接受规则”判定，没有确认阶段。

冒烟测试：用已有的 DeepSeek 运行 `fin-old20-ds-v15` 的轨迹，以 `evolve2-s1` 为基底跑一次提议，不计分。要求是：轨迹里能看到工具名；提议者能产出合法的改动和 proposal.md；能统计到提议者自身的成本。

提交，标签 `evolve2-s2`。

### 阶段 3：预注册与噪声

1. 先写 `evaluations/evolve-v2-20261104/PROTOCOL.md`，内容包括本文件的固定条件、接受规则、终局比较方法和能下的结论，并在任何计分运行之前提交。
2. 种子 `evolve2-s1` 在开发集上同时跑两次（各 `--concurrency 3`），运行名 `evo2-seed-a`、`evo2-seed-b`。
3. 噪声分布：两次种子运行之间的请求数比，按题自助抽样 4,000 次。取 10 分位数作为接受门槛，写入 `decisions/seed-null.json`。

如果门槛低于 0.75（也就是需要少 25% 以上的请求才算有效），说明开发集的噪声太大。此时停下来写报告说明情况，不进入阶段 4。

提交，标签 `evolve2-s3`。

### 阶段 4：进化

最多 2 代。第 1 代的两个候选中，只要有一个请求数比的点估计低于 1，或者有候选被接受，就跑第 2 代；否则在第 1 代后结束。

**接受规则**（每个候选都与同代、同时段的种子配对）：

- 请求数比低于噪声门槛；
- 通过数不比配对种子低超过 1；
- 正常交付且通过数不比配对种子低超过 1；
- 带警告完成数不比配对种子高超过 1。

两个候选都通过时，取请求数比低的那个，它成为下一代的基底。比较对象始终是 `evolve2-s1` 在同一时段的那次运行。

每代提交该代的产物和决策，标签 `evolve2-genN`。

### 阶段 5：留出集终局比较（只做一次）

没有候选被接受时，跳过本阶段。

否则，种子 `evolve2-s1` 与最终基底在 fresh30c 上做两轮比较，每轮两者同时跑，各 `--concurrency 3`。运行名 `evo2-ho-seed-r1/r2`、`evo2-ho-final-r1/r2`。

- 先提交全部清单，再发出任何模型请求。
- 主指标：两轮配对合并后的请求数比，90% 置信区间。
- 同时报告：成本比、通过数差（含置信区间）、正常交付且通过数差、带警告完成数，以及仓库类题子集单独的请求数比。
- 对最终基底相对种子新增的文字，检查是否出现 fresh30c 的题号或练习名。
- 跑完不再改动任何东西。

提交，标签 `evolve2-s5`。

### 阶段 6：报告

写 `evaluations/evolve-v2-20261104/REPORT.md`，中文：

- 开头一段给出结论和关键数字。
- 逐代表格：改动、引用的证据题、护栏结果、配对请求数比及置信区间、通过数、决策、提议者成本。
- 留出集表格。
- 改动审计：对照提议引用的轨迹核实证据，并检查是否削弱了验证或诚实报告的要求。
- 总花费：同时给出两种口径。一是按 GPT 公开价折算的美元等价，用于和第一轮比较；二是按 DeepSeek 官方价目重算的估计，价目以官方页面为准并注明查询日期。两者都不是实际账单。
- 与第一轮的对照：哪些变量变了，结论有没有变。

简历的写法：

- **只有**留出集请求数比的置信区间上界低于 1，且通过数差的置信区间不全落在负值区间时，才能在 `docs/resume-aster.md` 的“RSI 尝试”条目末尾补一个数字，形式为“DeepSeek 留出集请求数 −X%（90% 置信区间 [a, b]），通过数持平”。
- 否则不改简历，只在报告里写结论。

提交，标签 `evolve2-s6`。

## 不许做的事

- 除了阶段 1 列出的改动，不改 `src/aster/` 下的任何代码。
- 不改第一轮的任何文件，也不改 `evaluations/harness-v12-20261017/evaluate.py` 和题池。
- 预注册之后，不改门槛、接受规则和提议者提示词；不为了得到更好的结果而重跑任何运行或提议。供应商异常由剔除规则处理。如果某代超过一半的题被剔除，停下来报告。
- 终局比较之前，不看 fresh30c 的任何运行结果。
- 不在用户工作区直接运行 Aster；提议者只在私有目录的副本里运行。
- 全机同时最多 6 题。评测期间不拉取 docker 镜像，否则守护进程会超时。

## Git 规则

- 每个阶段一次提交加一个标签。不推送，不改写历史，不执行 `reset --hard` 和 `clean`。
- 每次提交前运行 `git diff --cached --name-only | grep -E '\.env|artifacts/private|deepseek\.toml'`，必须没有输出。
- 以下回退命令给用户使用，Codex 不执行：
  - 回到某个阶段：`git reset --hard evolve2-sN`
  - 放弃第二轮：`git switch exp/evolve-20261101`

## 预计规模

**运行次数**：阶段 3 跑 2 次开发集；阶段 4 每代 3 次，最多 2 代；阶段 5 跑 4 次 30 题。合计最多 12 次运行。

**花费**：按 GPT 公开价折算：
- DeepSeek 在仓库题上每题约 0.4 到 0.7 美元等价，依据是 old20 两次运行的 12 道仓库题，分别花了 4.43 和 8.14。
- 一次 20 题的开发集运行约 8 到 14。
- 一次 30 题的留出集运行约 4 到 6。
- 全部做完的上限约 120 美元等价。

DeepSeek 的实际账单远低于这个折算值，以官方价目为准。如果第 1 代后就按规则结束，约 50 美元等价。

**耗时**：DeepSeek 在仓库题上每题约 4 到 9 分钟，每代三次运行同时跑，每次约 1 到 1.5 小时。
