# 文档同步方案：课程、网站、故事文档、简历（2026-10-20）

执行者：Codex。本方案自带全部背景，不需要再读对话记录。

## 0. 现状

**代码已经改完，不需要改动 `src/`。** 最后一项缺陷修复是 `476f09f`（标签 `v15-infer`）：`Verifier.infer` 不再把“不要修改原始数据”这类对解法的约束误判为只读任务，也不再把 `dispatch` 里的 `patch`、`suffix` 里的 `fix`、“是怎么实现的”当成修改请求。新增 `tests/test_requirement_inference.py` 共 26 项（在旧代码上 15 项失败）；全量回归 1,464 项通过。

**文档落后于源码。** 课程各章最后更新于 2026-09-20 至 10-09，之后 `src/` 有 53 次提交。同步前的状态：00、06、07 三章的可运行实验因为模型可见格式变化而失败。

**工作区里已有一份未经复核的草稿。** 我先前让多个子代理同步了 `docs/learning/` 的 16 章，随后用户要求停止、改由 Codex 重写。当前工作区：

- 只有 `docs/learning/*.md` 这 16 个文件被修改，其他目录干净；
- 16 章的可运行实验全部通过（`run_examples.py` 各章，含原先失败的 00、06、07）；
- 代码块围栏成对，网站可以构建；
- **但篇幅普遍膨胀**：08 章 2.4 倍，06 和 02 章 1.9 倍，03、04、15 章 1.7–1.8 倍，14 章几乎没动（见下表）。共享说明里“不要把章节长度翻倍”这一条没有守住。

| 章节 | 原行数 → 现行数 | 倍数 | 处理建议 |
| --- | --- | ---: | --- |
| 00-quickstart | 166 → 193 | 1.2 | 保留，复核即可 |
| 01-agent-loop | 148 → 246 | 1.7 | 压缩实验 |
| 02-model-protocols | 153 → 285 | 1.9 | 压缩实验 |
| 03-tool-system | 216 → 366 | 1.7 | 压缩正文与实验 |
| 04-permissions-sandbox | 196 → 346 | 1.8 | 压缩正文与实验 |
| 05-state-and-evidence | 146 → 224 | 1.5 | 复核 |
| 06-context-budget | 278 → 522 | 1.9 | 压缩 |
| 07-context-archives | 271 → 353 | 1.3 | 复核 |
| 08-hooks-and-completion | 217 → 530 | 2.4 | **重点压缩** |
| 09-checkpoint-resume | 272 → 404 | 1.5 | 复核 |
| 10-layered-memory | 242 → 271 | 1.1 | 保留 |
| 11-memory-evolution | 261 → 370 | 1.4 | 复核 |
| 12-plans-and-skills | 235 → 278 | 1.2 | 保留 |
| 13-interaction-and-workers | 300 → 397 | 1.3 | 复核 |
| 14-evaluation | 259 → 260 | 1.0 | **几乎没同步，需要补写** |
| 15-rl-interface | 198 → 346 | 1.7 | 压缩 |

**第一步请先决定草稿的去留。** 草稿已另存为补丁 `/tmp/hb/learning-draft.patch`（3,683 行），`git stash` 或 `git checkout -- docs/learning` 之后仍可用 `git apply` 取回。两种做法都可以：

- **在草稿上复核压缩**（建议）：内容准确性已由子代理对照源码核过，实验也能跑通，省去重新查源码的成本；主要工作是删冗余、恢复原有节奏。
- **丢弃草稿重写**：`git checkout -- docs/learning`，按第 2 节自己重写。

无论哪种，最终都要满足第 5 节的验收标准。

## 1. 规则

1. **准确优先**：每条机制说明都要对照当前源码核实，引用源码沿用章节已有的相对链接写法，例如 `[verifier.py](../../src/aster/core/verifier.py)`。
2. **数字只来自报告**，并附链接；不要自己编，也不要自己重算后写成结论。可引用的来源见第 6 节。
3. **历史保留但标时间**：旧结论若已不成立，改写为“当时……；后来（日期、提交）改为……，原因是……”，不要让读者以为旧行为仍是现状。已经错误的说法必须改正，例如 08 章“`run_shell` 成功不会更新正式验证证据”。
4. **风格**：沿用章节原有结构（开头一句话总结、上下章导航、正文小节、`# 可运行实验` 代码块、预期输出、`## 练习与核对` 与 `<details><summary>参考答案</summary>`）。简体中文，保留既有术语（Harness、Runtime、Worker、token、checkpoint）与英文标识符。写给想读懂源码的学习者，讲清“为什么这样设计”，不要写成提交日志。
5. **篇幅**：每章在原长度的 1.3 倍以内；新机制多的 08 章可放宽到 1.6 倍。优先用表格和一句话结论，不要为每个机制都新增一个实验。
6. **可运行实验**：以 `# 可运行实验` 开头的代码块会被 `run_examples.py` 原样执行（离线、不调用模型）。必须全部通过，预期输出与实际一致，单个实验 240 秒内完成。不要为了覆盖机制而堆实验；一章 1–2 个为宜。
7. 不改 `src/` 和 `tests/`（唯一例外见第 5 节网站计数）。不推送。

## 2. 源码变更摘要（2026-10-09 之后，按主题）

对应章节写在每条后面。

**上下文与模型可见内容（05、06、07，兼及 00、02）**
- `ad63d26`（10-16）收据计量、只追加的历史、`[Context update]` 状态便笺。
- `c30153d`（10-17）模型可见的工具输出去掉记账字段。
- `fb753b3`（10-17）工具结果直接回答模型的下一个问题（编辑后回显改动行、补丁未匹配时给出最接近的当前行、搜索上下文行）。
- `8fb273c`（10-19）搜索结果按文件分组：每个文件一个标题，`12:` 为命中行，`11-` 为上下文行，重叠上下文只显示一次。
- `a774b25`（10-19）**证据与模型视图分离**（本轮最重要的设计，05 章主讲）：完整 JSON 记录留在 `_structured_history`、trace 和 checkpoint 中，供审计、恢复、预算、训练使用；组装请求时由 `src/aster/context/view.py` 的 `render_output` 渲染为纯文本（出错时的状态行、原文、必要页脚）；状态便笺改为可读文字（`render_workflow`）；新增 `workflow_notes` 设置。渲染是确定性纯函数，不破坏供应商前缀缓存。
- `8d5dbe1`（10-19）空结果写成 `[no matches]`、`[no files]` 等明确说法；Chat Completions 与 Anthropic 历史中正确解析工具名。
- `9d9ca97`（10-19）内容完整时不再加“完整输出见 observation_read”页脚；归档编号对模型显示 16 位前缀，`observation_read` 接受至少 12 位的唯一前缀。
- `ce2b79e`（10-19）证据引用 `[evidence …]` 只在 `memory_store` 已加载时显示。
- `3e51777`（10-19）工具参数 schema 去掉自动生成的样板（`compact_schema`，1,975 → 1,638 token）。
- `8128866`（10-17）精简系统提示词（359 → 248 token）；`01125ad`（10-19）“修 bug 先复现；实现缺失代码时先写再测”，并说明通过的测试结果会附带改动。

**工具（03）**
- `0c3ca67`（10-16）自己成功编辑后保留读取证据；`b2b195c` 正则搜索。
- `98f0cfb`（10-17）一批调用里某个参数错误时，其余只读调用照常执行；记录分阶段耗时。
- `24e0f25`（10-18）`patch_file` 在只差空白时做唯一匹配；提示可并行编辑多个文件。
- `9180c90`（10-18）`search` 参数小错误自动修正（`context` 超限截到 10、字段名粘连等）。
- `a50a0ab`（10-19）`read_file` 接受单个 `line`；`python -c` 或 heredoc 形式的断言脚本作为内联测试运行。
- `5578380`（10-18）`service_start` / `service_status` / `service_stop`：跨工具调用存活的服务。
- `3971692`（10-18）沙箱 HOME 移到 `.tmp/home`；通过的测试结果附带本次任务的改动（`01125ad` 改为短 diff 全文）。

**执行环境与沙箱（04）**
- `7a5d074`（10-17）命令里的工作区绝对路径映射到副本（`map_root`）。
- `60b15d8`（10-18）工作区不含敏感文件时，副本中的命令可只读访问原工作区（`exposable`、`readable_roots`、`--readable`）；Landlock 管不到 chmod，运行时快照并恢复原工作区权限位（`_restore_modes`）。
- `f3f2a1f`（10-19）程序按绝对路径写原工作区失败时，输出末尾附上原因和做法（`ABSOLUTE_WRITE_NOTE`）。
- `b7bafba` / `aff2022` / `64f3ffe`（10-17）增量快照（stat 缓存、racy 窗口）、按内容寻址的基线、checkpoint 组提交（fsync 间隔 ≥5 秒）、SQLite WAL、trace 追加不重读。（09 章）
- 04 章可以强调：用户明确选择保留“副本中执行、校验后发布”的模型，不在真实工作区直接执行命令，理由是直接执行的 Agent 一条错误的删除命令就可能造成不可挽回的损失。不要写成个人经历。

**验证与完成检查（08 主讲）**
- `1c9ad94`（10-09）未解决的失败跨验证保留。
- `119f741`（10-17）只因断网失败的测试按运行时证据归为环境事实。
- `4e15809`（10-17）Jest 逐用例结果。
- `8290388` → `30bd0b3`（10-16/17）“由模型声明既有失败”的方案被撤回，原因见 `docs/harness-v12.md` 第 5 节。
- `f4400d6`（10-17）运行时在原始代码上复跑失败测试、比较失败签名，作为“既有失败”的证据；被驳回过一次且当前代码上仍以相同签名失败，才不再阻塞。
- `756536e`、`68ea98f`、`68d69d3`（10-18）项目自带的构建加测试命令按退出码判定（只接受 `&&` 串联的普通命令）；退出码失败只把验证状态置为失败，不成为必须消除的义务；测试副本中留下不发布的文件不再丢弃测试证据。
- `1a30d4f`、`edd563d`（10-18）只含断言的内联脚本作为一个测试运行，其失败同样不成为义务。
- `1fec769`（10-18）在 `run_shell` 中执行的测试命令同样计为正式验证。
- `e110f1b`（10-18）、`b7870ea`（10-19）交付前由运行时在最终代码上重跑过期的既有失败与最后一次通过的测试命令；代码风格检查不算测试。
- `053b8f4`、`6147663`（10-19）原始代码复跑也能读真实工作区路径；失败签名忽略副本内的相对临时路径。
- `476f09f`（10-20）`Verifier.infer` 修复，见第 0 节。
- **核心原则**（值得在 08 章讲清楚）：只有带真实用例身份的失败，才必须由同一个用例重新通过来消除；没有用例身份的证据（退出码、共用一个 id 的断言脚本）只影响验证状态。被否决的“最新结果覆盖”方案见 `docs/harness-v13.md` 第 4 节。
- **完成检查对照实验**（`evaluations/acceptance-ablation-20261020/REPORT.md`）：GPT × fresh30b，严格 / 产品默认 / 关闭三组，在共同可比的 14 题上都通过 13 题。要点有三条：
  - 门禁很少触发。补做次数（`final_revisions`）三组分别为 3、4、0 次，没有救回任何一题。
  - 关闭它也没有省钱。共同可比的 14 题上，关闭组比默认组请求多 13.3%、已知费用高 6.5%；全表的成本下降主要来自 `terminal-write-compressor` 一题提前被供应商中断（关闭组少的费用里约 61.4% 出自这一题），不能算作门禁的开销。
  - 强模型本来就会自己验证。关闭门禁后 30 题中有 8 题以 `provider_error` 结束，其余 22 题里 19 题仍以 `verified` 结束，只有 3 题是带警告交付，其中 1 题（`astropy-12907`）独立判分失败——而这题在门禁打开时也失败。这三个数在 REPORT 里没有成表，用第 6 节的脚本从 `comparison.json` 复算后再写。
  - 注意评测脚本对所有题强制“必须修改 + 必须验证”，比产品默认更严。产品默认按提示推断，这 30 题里只有 18 题（8 道仓库题加 10 道 Aider 题）要求测试，10 道 BigCodeBench 和 2 道终端题不要求。
  结论：产品默认保留，作用是低成本的保险和诚实标注（没有验证的交付标为 `completed_with_warning`），它的用武之地是弱模型，而这一点尚未验证。08 章和 14 章都应引用这个结论，**不要把门禁写成提升质量的卖点**。

**模型协议与运行时（02）**
- `c6b651d`（10-09）中断的流式响应重试。
- `7abdba4`（10-19）回复在输出上限处被截断时，追加一句提示后重试，每次运行最多 2 次（`OUTPUT_LIMIT_CODES`、`OUTPUT_LIMIT_RECOVERIES`、`OUTPUT_LIMIT_NOTE`）；截断内容不执行、不进历史；其他未完成原因（内容过滤、`pause_turn`）仍直接终止。
- `1fa30a3`、`937fb49`（10-18）单次运行不启动后台记忆整理（`oneshot_maintenance_seconds`、`background_maintenance`）。（00、10、11 章）
- 评测已接入第二个模型家族：DeepSeek 官方 Chat Completions（非流式）；本地开源模型也走这条路。

**训练接口（15）**
- `b7d2128`（10-18）`src/aster/training/rollout.py`、`qwen.py`：`training_settings`、Qwen 工具调用解析（hermes JSON 与 XML 参数）、`AsterEnv.step_turn` / `step_invalid`、按 token 记录的采样轨迹（prompt/completion token、logprob、loss mask、分段、终止标签）、`VLLMCompletions`。详见 `docs/agent-rl-harness.md`。**尚未在 GPU 上实测**，R0 与 R4–R7 未做（见 `docs/v14-and-agent-rl-plan.md`）。
- 15 章要讲清：为什么必须“token 进、token 出”（重新分词会让训练用的 token 与采样时不一致）；上下文被改写时按段切样本；基础设施失败不进梯度。可新增一个离线实验，参考 `tests/test_token_rollout.py` 里逐字符的假分词器与脚本化生成器。

**评测方法（14 章重点，当前几乎没同步）**
需要补上 10 月中旬之后形成的方法，并说明每项防的是什么问题：
1. 加权成本口径：`(未缓存输入 × 1.25 + 缓存输入 × 0.125 + 输出 × 10) / 1e6`，按公开价折算的美元，**不是账单**；缺失用量不视为免费，也不默认外推。
2. 判分环境漂移与 tarpit 判分网络（`REPORT.md` 第 5 节）。
3. 留出集：先抽题、预检、冻结，再评测；看过轨迹的题集不再当留出集。抽题规则见 `evaluations/fresh30-20261018/prepare.py`、`evaluations/fresh30b-20261018/prepare.py`，结论见 `REPORT.md` 第 9、11、12 节。
4. 同时段配对运行、按题配对的 bootstrap 置信区间（`evaluations/fresh30-20261018/analyze.py`）；单次 20–30 题的波动约 ±2 题。
5. 第二个模型家族用来检验改动是否与模型无关（`REPORT.md` 第 15 节、`docs/harness-v15.md`）。
6. 消融实验的两个例子：BigCodeBench 视图消融（`REPORT.md` 15.1）、完成检查对照（`acceptance-ablation-20261020`）。
7. “便宜的失败”：因截断提前崩溃的运行请求数反而更少，比较时必须区分（`REPORT.md` 15.3、15.4）。
旧内容（截至 10-09 的评测）保留为历史并与新方法衔接。

## 3. 其他文档的过时说法（已逐条核对行号）

| 文件:行 | 现在的说法 | 应改为 |
| --- | --- | --- |
| `docs/configuration.md:41` | “真实模型评测使用 Responses” | 2026-10-19 起评测同时使用 DeepSeek 官方 Chat Completions；Anthropic 仍只有契约测试 |
| `docs/implementation.md:35` | “真实模型使用 GPT-5.6 Luna / Responses” | 同上 |
| `docs/resume-draft.md:70` | “真实连接只评测了 Responses” | 同上 |
| `README.md:208` | “当前流式响应仅支持 Responses……已归档的真实服务验证主要使用 Responses” | 流式仅 Responses 仍成立；真实服务验证现在包含 DeepSeek 官方 Chat Completions（非流式） |
| `docs/configuration.md:52` | “到达输出上限的响应按 `incomplete_response` 停止，未完成的工具参数不会执行” | 先追加一句提示重试，每次运行最多 2 次；两次都被截断才按 `incomplete_response` 停止。截断内容始终不执行、不进历史 |
| `docs/learning/README.md:13` | “日常 CLI/TUI 使用默认开启项目记忆、记忆演化和自动整理” | 交互式会话仍默认整理；单次 CLI 任务默认不发起后台整理（`oneshot_maintenance_seconds` 默认 0） |
| `docs/configuration.md` 表格 | 缺 `workflow_notes`（`src/aster/config.py:187`，默认 `true`） | 补一行。`oneshot_maintenance_seconds`、`background_maintenance` 已在第 140–141 行，不用动 |

## 4. 故事文档与简历

**`docs/learning/project-story.md`**（544 行）目前记录到 2026-10-11。需要补一节覆盖 10-12 至 10-20，插在第十一节（真实仓库评测，第 416 行）之后、原第十二节“这些演进留下了怎样的设计方法”（第 477 行）之前，于是原十二、十三顺延为十三、十四。同时要改第 7 行的“记录范围为 2026 年 9 月 11 日至 10 月 11 日”，并补全末节的时间线表格。建议的小节（沿用全文“先问题、再取舍、再结果、再局限”的写法）：

1. **先量成本，再谈优化**：分阶段计时发现 63% 的任务时间在 harness 自身；容器 overlayfs 上每次 fsync 88–225 ms；修复后单步开销降低 6.7–13 倍，模型之外的时间降低 81%（`docs/harness-v12.md`、`REPORT.md` 第 4、7 节）。
2. **判分环境漂移与留出集**：主机网络应答黑洞地址导致官方参考补丁也过不了，tarpit 网络修正后 24/24 历史结果复现；fresh30、fresh30b 的冻结协议（`REPORT.md` 第 5、9、11、12 节）。
3. **逐轮对比找出多余请求**：83 个多余请求的来源分解（后台记忆 17.5、收尾阶段 55.5、探索与迭代基本持平），以及据此做的 v14 改动（`docs/v14-and-agent-rl-plan.md` A.1、A.2）。
4. **证据与模型视图分离**：为什么分开、离线回放的估算与真实评测的差异、两个模型合并后每请求输入 −9.8%（95% 置信区间 [−14.2%, −5.1%]）而总成本不显著（`docs/harness-v15.md`、`REPORT.md` 第 15 节）。
5. **完成检查到底值不值**：这是全文最该诚实的一节。它从第一天就在验收契约里（2026-09-11 的 `docs/implementation.md` 已有“完成契约”，`verifier.py` 的首版注释就是“任务成功不来自循环结束”），不是为基准测试补的；但评测脚本把它开到最严，比产品默认严得多。对照实验在强模型上没有测出质量收益：补做次数三组 3/4/0，关闭后 22 道正常结束的题里 19 道仍自己跑了测试。结论是产品默认保留（低成本保险加诚实标注），不作为质量卖点，它的用武之地是弱模型，而这一点尚未验证（`evaluations/acceptance-ablation-20261020/REPORT.md`，数字见第 2 节与第 6 节脚本）。
6. **规则堆叠的代价**：`tools/testing.py` 从 404 行增长到 802 行、21 处正则；每接触一类新任务就冒一个新漏洞（编译后运行、`npm run lint`、`python -c` 断言脚本、相对临时路径、`without changing` 误判）。这说明抽象选择本身有待改进，不是规则不够多。这一节也适合放进第十二节的“设计方法”。
7. **两个模型家族**：为什么只用一个强模型验证不足以说明“与模型无关”，以及 DeepSeek 上暴露的两个缺陷（Chat 历史认不出工具名、输出截断直接终止）。

**简历**：`docs/resume-v12.md` 已有 v13、v14、v15 三节与 23 个面试追问，内容是准确的，但结构已经按版本堆叠。建议整理为按能力归类的一份（瓶颈定位与降本、执行环境与安全、验证与完成语义、评测方法、训练接口），并把第 0 节的“最终数字”统一到 `REPORT.md` 第 15 节的口径。把下面两条写进去，它们最经得起追问：

- “在强模型上没有测出完成检查的质量收益，所以产品保留它作为低成本保险和诚实标注，而不把它当卖点。”
- “每请求输入在两个模型、两套题上一致下降 9.8%（置信区间不跨 0），但总成本不显著，因为请求数由少数长任务和模型解题路线决定；还要区分‘便宜的失败’。”

`docs/resume-draft.md` 的第 70 行见第 3 节。

## 5. 分阶段执行与验收

分支 `docs/sync-20261020`，起点标签 `docs-sync-baseline`。每阶段一个提交加一个标签，不推送。回滚：`git switch -c rollback-sN <上一阶段标签>`。

| 阶段 | 内容 | 标签 |
| --- | --- | --- |
| S0 | 建分支与起点标签；决定草稿去留（保留或 `git checkout -- docs/learning`） | `docs-sync-baseline` |
| S1 | 00–07 章 | `docs-sync-s1` |
| S2 | 08–15 章（14 章需补写，08 章重点压缩） | `docs-sync-s2` |
| S3 | 第 3 节的其他文档；`docs/learning/README.md` 的路线说明 | `docs-sync-s3` |
| S4 | `project-story.md` 新增一节并同步时间线 | `docs-sync-s4` |
| S5 | 重建网站、更新网站计数、简历整理 | `docs-sync-s5` |

每个阶段提交前都要通过：

```bash
/home/dzh/.conda/envs/aster/bin/python docs/learning/run_examples.py all   # 16 章实验，离线
/home/dzh/.conda/envs/aster/bin/python -m ruff check src tests scripts learn_web/scripts learn_web/tests
/home/dzh/.conda/envs/aster/bin/python -m pytest -q -m core                # 不调用模型
```

S5 还要：

```bash
/home/dzh/.conda/envs/aster/bin/python learn_web/build.py
/home/dzh/.conda/envs/aster/bin/python -m pytest -q learn_web/tests/test_site.py
```

**网站计数需要更新。** `learn_web/tests/test_site.py` 有四个硬编码的总量门槛，用来防止代码块和图在构建中被静默丢掉。章节内容增长后要改成当前实际值，并在提交说明里写明原因：

| 行 | 断言 | 停止前草稿的实际值 |
| ---: | --- | ---: |
| 217 | `len(expected) == len(actual) == 79` | 112 |
| 219 | `answers == actual_answers == 8` | 8（不变） |
| 238 | `len(expected) == len(actual) == 14` | 14（不变，除非新增 mermaid 图） |
| 239 | `sum(len(nodes) …) == 120` | 126 |
| 240 | `sum(len(edges) …) == 119` | 125 |

最终以压缩后的实际值为准，用下面的脚本复算，不要直接抄这几个数（已验证可运行）：

```bash
cd /home/dzh/Aster && /home/dzh/.conda/envs/aster/bin/python - <<'PY'
import sys; sys.path.insert(0, 'learn_web/tests')
from test_site import MARKDOWN, markdown_blocks, diagram_contract
code = answers = diagrams = nodes = edges = 0
for s in MARKDOWN:
    code += sum(1 for lang, _ in markdown_blocks(s) if lang != "mermaid")
    answers += s.read_text(encoding="utf-8").count("<summary>参考答案</summary>")
    for lang, body in markdown_blocks(s):
        if lang == "mermaid":
            n, e = diagram_contract(body); diagrams += 1; nodes += len(n); edges += len(e)
print(f"代码块 {code} 参考答案 {answers} 图 {diagrams} 节点 {nodes} 边 {edges}")
PY
```

`assert actual == expected` 这两条逐块比对必须保持原样，不要放宽。

提交信息末尾加：

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

## 6. 可引用的资料

- 设计说明：`docs/harness-v12.md`（开销、执行环境、评测可信度、既有失败证据、精简提示词）、`docs/harness-v13.md`（服务、退出码验证、副本中不发布的文件、内联断言脚本、被否决的“最新结果覆盖”）、`docs/v14-and-agent-rl-plan.md`（v14 方案与 D 节执行结果）、`docs/harness-v15.md`（模型视图分离、工具定义精简、截断恢复、两个模型的评测）、`docs/agent-rl-harness.md`、`docs/execution-environment.md`、`docs/configuration.md`。
- 评测报告：`evaluations/harness-v12-20261017/REPORT.md` 第 1–15 节、`evaluations/acceptance-ablation-20261020/REPORT.md`、`evaluations/cost-summary-20261016.md`（v9–v11 降本总结）。
- 用 `git show <提交>` 查看每项改动的完整说明与代码。

完成检查对照里“三组各自怎么结束的”没有成表，用这个脚本复算（已验证可运行）：

```bash
cd /home/dzh/Aster && /home/dzh/.conda/envs/aster/bin/python -I - <<'PY'
import collections, json
d = json.load(open('evaluations/acceptance-ablation-20261020/comparison.json'))
for arm in ('benchmark', 'default', 'off'):
    rows = [v[arm] for v in d['per_task'].values() if v.get(arm) and v[arm].get('state_available')]
    print(arm, '有终态 %d' % len(rows),
          '补做 %d' % sum(r.get('final_revisions') or 0 for r in rows),
          dict(collections.Counter(r['stop_reason'] for r in rows)))
PY
```

当前输出（2026-10-20）：严格 30 题有终态、补做 3 次、27 题 `verified`；默认 29 题、补做 4 次、21 题 `verified`；关闭 30 题、补做 0 次、19 题 `verified` 加 3 题 `completed_with_warning`，8 题 `provider_error`。

## 7. 注意事项

1. **不要为每个机制都加实验。** 停止前的草稿里 01、02、08 章各有 2–4 个实验，是篇幅膨胀的主要来源。优先用表格和一句话结论。
2. **`recheck_preexisting` 不适合做离线实验**：它要在沙箱里真跑测试、依赖 Landlock，不满足“离线、确定”。在正文中讲清，并指向 `tests/test_preexisting_failures.py`。
3. **`_structured_history` 是私有属性**：实验里只读它来观察时，要注明“不是调用方应该依赖的接口”。
4. **熔断器尚未进入任何章节**：`ModelClient.complete()` 外层有熔断（`src/aster/safety/resilience.py`，`0ce78db`，默认连续 3 次失败后冷却 30 秒），目前只有 09 章提到相关设置不进 checkpoint identity。补在 02 或 13 章都合适，属于可选项。
5. **不要把门禁写成质量卖点**，理由见第 2 节末尾。
