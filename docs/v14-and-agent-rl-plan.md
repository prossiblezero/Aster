# v14 优化方案与 Agent RL 准备度判断（2026-10-18）

本文分两部分：A 是 harness 本身的下一轮优化（v14），B 是“在 Aster 上对开源 Qwen 模型做 SFT→RL”的准备度判断和补齐路线。所有判断都基于三项调研：

- fresh30 上 Aster、pi、Codex 的逐轮对比（分析脚本在 `/tmp/turngap/`，结论摘要见 A.1）；
- 训练接口的代码审计（`src/aster/training/`、上下文构建、协议编解码、运行时）；
- 文献与开源框架调研（见 B.4，arXiv 编号已由子代理在 arxiv.org 上核对过标题和日期，我没有逐篇读全文）。

## 0. 先定评测协议：fresh30 已经“用掉”了

A 部分的问题是从 fresh30 的轨迹中找出来的。如果 v14 再拿 fresh30 来证明提升，就是看了答案再改卷子。因此：

1. **动手写代码之前，先抽一套新的留出集 fresh30b 并冻结。** 抽法与 fresh30 相同：同样的构成、新种子、排除所有用过的题、预检后打标签。
2. 开发只使用 old20、new30 和 fresh30，都按开发集对待。
3. v14 完成后，在 fresh30b 上同日运行 v13-final 和 v14 各两次、pi 和 Codex 各一次，按题配对，用 bootstrap 给出置信区间。只看，不改。

## A. v14：把多出来的请求降下来

### A.1 证据：83 个多余请求从哪来

fresh30 上 v13 平均 356 个请求，pi 273 个，Codex 253 个。

| 来源 | 每 30 题多出的请求 | 说明 |
| --- | ---: | --- |
| 后台记忆维护请求 | 17.5 | 自动记忆开启时的 `memory_changes` 请求，计入了请求总数，但不是 agent 回合。pi 和 Codex 没有这类请求 |
| 收尾阶段（最后一次编辑之后） | 55.5 | Aster 130.5 回合，pi 75，Codex 92 |
| 探索阶段 | 9.5 | 基本持平 |
| 迭代阶段（编辑和修复） | 0.5 | 持平（114.5 对 114） |

收尾阶段多出来的部分：

- **完成门禁驳回及其后续：每轮约 22–30 回合。** 被驳回的 7–10 道题两轮全部通过判分；驳回之后只出现 4 个编辑回合，其中 3 个是越权修改 `conftest.py`。这些回合没有改变任何结果。
- **没有编辑就重跑验证：** Aster 60 次，pi 27 次，Codex 34 次。其中每轮 7–9 次是先用 `run_shell` 跑通，再用 `run_tests` 重跑一遍，因为提示说 `run_shell` 不算正式验证。
- **`run_tests` 的运行环境与 `run_shell` 不同：** 它在快照副本中用 `python -I` 包装运行，HOME 也被改写。由此产生的问题：
  - 终端题读取 `/app/*.json` 出现 PermissionError，模型追了 6 个回合 chmod，有一次执行了 `chmod 777 /app`；
  - matplotlib 遇到 `-nspkg.pth` 收集错误；
  - astropy 出现 runner_error。

  同样的命令，pi 用 bash 一次就跑通。
- **工具调用被拦截：每轮约 14.5 个回合整轮作废。** 主要是 `patch_nonunique` 和 `search` 参数无效。
- **编辑串行化：** Aster 从不在一个回合里发两个编辑，每轮有 13 次“先改 .h、下一回合改 .cpp”。Codex 一次 `apply_patch` 平均改 1.3 个文件。另有每轮 11.5 个只看 diff 的回合（pi 4 个）。

真正“买到质量”的验证（测试失败后紧接着修源码）每轮只有 7–9 次。首次验证（每轮约 28.5 次）三家相同，应当保留。

**过拟合风险：** 门禁问题集中在 4 道 C++ 题和 pytest-7205，运行环境问题集中在 2 道终端题和 astropy、matplotlib。下面每一项改动针对的都是通用规则，不是单题；最终效果以 fresh30b 为准。

### A.2 分阶段改动

每一阶段都在 `perf/harness-v14` 分支上，由 `v13-final` 打出起点标签 `v14-baseline`，每阶段一个提交加一个标签 `v14-sN`。回滚方式：`git switch -c rollback-sN v14-s(N-1)`，在新分支上回到上一阶段，不改写历史。每阶段都必须通过全量回归。

**S1. 统一执行路径：测试与 shell 在同一环境运行（重构，预计收益最大）**

现状是测试在独立的快照副本中运行，环境与 `run_shell` 不同，这是环境类失败的根源；而且测试只能用 `run_tests`，导致同一条命令跑两遍。

改法：

- 删除 `run_tests` 的独立执行器，测试与 `run_shell` 走同一个沙箱执行路径：同样的 uid、HOME、路径映射，不加 `-I`。
- 证据采集改为在这条路径上附加：
  - pytest 通过 `PYTEST_ADDOPTS=--junitxml=<工作区外路径>` 获得逐用例结果，不需要改写命令；
  - Jest、go test 沿用现有的结构化报告参数；
  - 其他项目测试命令按退出码判定（沿用 v13 的普通 `&&` 链规则）。
- `run_shell` 执行的命令只要被识别为测试，就自动生成正式验证证据，不必再用 `run_tests` 重跑。`run_tests` 保留为别名，不再是独立实现。
- 证据仍然绑定工作区哈希，取命令执行前后各一次。只要命令改动了工作区，证据就绑定执行后的版本。

预期收益：消除权限追逐和环境类失败，以及“shell 跑完再用 run_tests 重跑”的回合。估计每 30 题省 15–25 个请求。

风险：

- 测试可能写入工作区（缓存、构建产物）。已有的 `IGNORED` 和 S5 的产物过滤会处理这些文件。
- 内联测试（`code=`）仍需临时文件，这些文件放在工作区之外。

**S2. 完成门禁：每组失败最多驳回一次（需要你决定，见第 C 节）**

现状：

- 在原始代码上以相同签名失败的测试，只有在当前代码上重跑仍相同时才放行；
- 改过代码之后要再重跑；
- 门禁可以反复驳回。

可选方案：

- 方案 1：只驳回一次。第二次结束时，原始代码上相同签名的失败直接列为“既有失败，未验证”并放行。
- 方案 2：保持现状。

你在 v11 时否决过“由模型声明豁免”。方案 1 依然由运行时根据证据判定，不是模型声明，但它确实放宽了完成语义，所以需要你来决定。

预期收益：方案 1 每 30 题省约 10–20 个请求。

**S3. 一次性运行不做后台记忆维护（需要你决定）**

现状是单次 CLI 和评测都开启自动记忆，每次任务结束后发出记忆维护请求，平均每题约 0.6 个。

可选方案：

- 方案 1：单次运行默认关闭自动整理；交互式会话保持开启。
- 方案 2：保持开启，但评测报告中单列这部分成本。

这是产品默认值的变化，所以列为待决定项。

**S4. 编辑工具：一次改多个文件，锚点更宽容**

- `patch_file` 接受多个文件的编辑，同一批内原子生效。
- `patch_nonunique` 时返回每个候选位置的行号，允许用 `occurrence` 或起始行号指定位置。
- `search` 的参数校验放宽：未知参数忽略并提示，而不是整次拒绝。

估计每 30 题省 8–15 个请求。

**S5. 收尾信息合并**

- 测试结果中附带 `git diff --stat`，模型不必再单独花一回合看 diff。
- `changed_paths` 和 `git_diff` 排除沙箱产物（`.npm/`、`build/`、`.tmp/` 等）。

估计每 30 题省 5–10 个请求。

**S6. 评测**

- 开发集：old20 和 new30 各跑一次，作为冒烟检查。
- 留出集：按第 0 节在 fresh30b 上评测。
- 判定标准（事先写死）：
  - 请求数配对差的置信区间上界小于 0，算成本显著下降；
  - 同时独立通过的配对差不能显著为负，即置信区间下界不低于 −2。

## B. 在 Aster 上做 SFT→RL：判断与补齐路线

### B.1 判断：骨架合适，现在还不能直接开训

**已经具备的基础**（这些比多数个人项目好）：

- 产品、评测和训练环境共用同一个 `Runtime`。`AsterEnv` 的请求与自主运行逐字一致，有测试保证（`tests/test_context_policy_input.py`）。
- 奖励与完成判定分离：
  - 没有独立 verifier 时不给成功奖励；
  - 预算耗尽时仍对最后一版候选评分；
  - 数据契约已经定义了 SFT 投影、assistant-only mask、GRPO 分组校验和逐 token logprob 字段（`src/aster/training/contracts.py`）。
- 评测 runner 已经能做到：
  - 每题一个 docker 容器；
  - 判分在全新的容器中进行，使用 tarpit 网络；
  - 跑多个 Agent 和多次重复；
  - 冻结留出集并做配对 bootstrap。

  RL 的环境池和奖励器可以直接从这里演化。
- 能完整记录每次请求、每个阶段的时间和每次工具结果，便于排查训练中的异常轨迹。

**阻塞开训的 6 个缺口：**

| # | 缺口 | 为什么阻塞 | 证据 |
| --- | --- | --- | --- |
| 1 | **没有 token 级记录（token-in-token-out）** | env 只接收解析好的动作，不记录 token id 和 logprob。RL 框架的共同要求是不能对解码后的文本重新分词（SkyRL 文档、verl 的 agent loop） | `training/env.py:86-92`；`providers/codec.py:160-171` 的 chat payload 没有 logprobs 和 seed |
| 2 | **历史会被改写** | 上下文接近预算时，旧工具输出会被替换成 `[archived…]`，整组旧交换会被删除，而且是写回历史的。32k 上下文的小模型会频繁触发。训练时必须把每次改写之后的部分当作独立样本，或者关闭改写 | `context/budget.py:299-375`；`context/builder.py:45-47` |
| 3 | **产品行为混在训练分布里** | `[Context update]` 状态提示、`before_final_answer` 都没有开关；门禁的驳回消息也会进入轨迹 | `context/prompt.py:147-200` |
| 4 | **env 不在容器里，也不能并行** | 评测走 `Runtime.run`，不经过 `AsterEnv`；`AsterEnv` 只在宿主上复制目录，没有 docker，也没有 RPC 服务，每步还会同步哈希整个工作区 | `env.py:45-52`；`storage/workspace.py:226` |
| 5 | **奖励防作弊不足** | agent 可以改测试文件和 conftest；评测容器里的 git 历史没有清理。文献报告 rollout 中翻 git 历史找答案的比例达 21%、篡改 harness 的比例达 8.3%（arXiv:2606.26300，此数据来自搜索摘要，未读全文） | `docs/post-training-readiness.md` 自认；评测 runner 没有删除 remotes、branches、tags |
| 6 | **Qwen 工具调用的健壮性** | 本地 vLLM 用 hermes 解析器时，参数 JSON 不合法或 `finish_reason=length` 会直接让整个 episode 以错误结束，而 RL 需要把这些情况作为观察反馈给模型 | `providers/client.py:127-153`；`codec.py:244-271` |

另外还有两项资源约束：

- **GPU**：两张 RTX 5090 正被其他任务占用（检查时一张利用率 99%、占 8 GB，另一张占 21 GB）。
- **磁盘**：只剩 124 GB。一个 SWE-bench 镜像就有几 GB，放不下大规模的仓库题环境。

### B.2 现实的目标

| 目标 | 可行性 | 依据 |
| --- | --- | --- |
| Qwen3-8B 或 Qwen3-4B-Instruct-2507 的 LoRA SFT（用 Aster 轨迹） | 可行 | 两张 32 GB 卡，bf16 加 LoRA 加梯度检查点，序列长度 16–32k |
| 在轻量任务上做 GRPO：单文件或小仓库（Aider、BigCodeBench 类），镜像小，测试几秒钟 | 可行，规模小 | 一张卡跑 vLLM 推理，一张卡训练 LoRA；4B 较宽裕，8B 的 KV cache 偏紧 |
| SWE-bench 级仓库题 RL | 本机不可行 | 公开配方都用 32–64 张 H100：DeepSWE 用 64 张 H100、6 天；ProRL Agent 用 32 张 H100（arXiv:2603.18815） |
| 预期效果 | SFT 是主要收益，RL 是锦上添花 | 小模型只做 RL 的公开增益是 +3 到 +8 个点（SkyRL-v0；arXiv:2603.18815）。最强的小模型结果来自 DAgger 式的轮级师生混合：4B 达到 27.3%（arXiv:2605.12913） |

对简历来说，最有价值的不是分数，而是一个能讲清楚的完整闭环：同一个 harness 用于教师采样、拒绝采样 SFT、在线 RL 和留出评测，并且做到 token 级一致、按段切样本、奖励防作弊、基础设施失败不进梯度。

### B.3 补齐路线

起点标签为 `rl-baseline`，每阶段一个提交加一个标签 `rl-sN`。回滚方式与 A 相同。

**R1. 训练配置档 `profile="train"`**

- 关闭 memory、dream 和 worker。
- 为状态提示、`before_final_answer` 和门禁分别加开关；默认全部关闭，奖励只来自独立 verifier。
- 固定 id、路径和 capabilities 文本，保证可复现。
- 每一步断言上下文没有被改写；如果改写了，必须按 R3 切段。

**R2. 以模型原生聊天模板为准的策略接口**

- 新增 `TokenPolicy` 接口，对接本地 vLLM。可以用 `/v1/completions` 直接传 token id，也可以用 chat 接口加 `return_token_ids` 和 `logprobs`。
- 在 Aster 内渲染 Qwen 聊天模板，并解析 hermes 格式的 `<tool_call>`。
- 工具调用 JSON 不合法或输出被截断时，作为观察反馈给模型，不结束 episode。

**R3. 轨迹记录器**

- 每个 assistant 回合记录：prompt token id、response token id、逐 token logprob、loss mask。
- 上下文被改写时自动开始新的一段，同一 episode 的各段共享奖励（CompactionRL arXiv:2607.05378、SUPO arXiv:2510.06727 的做法）。
- 每个 episode 打上标签：正常结束、超时、基础设施失败、上下文或回合数达到上限。基础设施失败不进梯度，达到上限的轨迹默认也不进（DeepSWE 的 compact filtering）。
- 导出格式兼容 TRL 和 SkyRL。

**R4. 容器化的环境服务**

- 把 `AsterEnv` 放进任务容器，复用评测 runner 的容器管理、判分容器和 tarpit 网络。
- 通过 socket 提供 `reset` / `step` 接口，支持预热容器池和并行 episode。
- 奖励器：
  - 判分前把测试文件恢复成原样；
  - 删除 git remotes、branches 和 tags；
  - 判分时禁止网络；
  - 检测并标记修改测试文件、修改 conftest、翻查 `git log` 的行为。

**R5. SFT 数据**

- 训练集来源：SWE-smith（arXiv:2504.21798）或 R2E-Gym（arXiv:2504.07164）中镜像较小的子集，加上 Aider 和 BigCodeBench 类任务。必须与 old20、new30、fresh30、fresh30b 按仓库和题目家族完全隔离。
- 用 gpt-5.6-luna 在 Aster 中采样教师轨迹，只保留通过隐藏测试的，再做长度上限和去重。
- 成本：按 fresh30 上 v13 每题约 0.09 美元计算，1,000 道题、每题采 2 条，约 180 美元。
- 公开的 Nebius OpenHands 轨迹（CC-BY-4.0）工具集与 Aster 不同，只适合做格式转换实验，不作为主数据。

**R6. SFT 训练与评测**

- 用 TRL 或 LLaMA-Factory 做 LoRA SFT。
- 在 Aster 中评测 base 和 SFT 后的模型，使用同一套冻结留出集和配对 bootstrap。

**R7. RL 先打通，再扩规模**

- 框架选择：先做一个最小验证（spike），候选是 SkyRL（gym 风格 API、异步、LoRA，文档推荐 token-in-token-out 的 harness，与 Aster 最接近）和 verl 的 AgentLoop（token-in-token-out，内置 hermes 工具调用）。
- 算法：GRPO 或 DAPO，每题 8 条 rollout，并丢弃组内全对或全错的分组。
- 规模：在 4B 上从 100–300 道轻量题起步。

### B.4 文献与框架要点（子代理调研，编号已核对）

| 类别 | 工作 |
| --- | --- |
| 数据与 SFT | SWE-Gym（2412.21139，ICML 2025）、SWE-smith（2504.21798）、R2E-Gym（2504.07164）、Skywork-SWE（2506.19290）、SWE-rebench（2505.20411）、Kimi-Dev（2509.23045） |
| 多轮 RL | DeepSWE（Agentica 模型卡：Qwen3-32B，23%→42.2%）、Nebius 的 RFT 加 DAPO（2508.03501）、SkyRL-Agent（2511.16108）、ProRL Agent（2603.18815）、DAgger for LM agents（2605.12913）、LEGO-RL（2608.17393，通过代理训练不经修改的 harness） |
| 上下文与训练 | CompactionRL（2607.05378）、SUPO（2510.06727） |
| 算法 | DAPO（2503.14476）、GSPO（2507.18071）、GiGPO（2505.10978） |
| 奖励作弊 | 2606.26300、BenchJack（2605.12673） |

框架：

- verl：AgentLoop，token-in-token-out；
- SkyRL：gym 风格 API，异步；
- rLLM：替换 OpenAI base_url 接入；
- TRL：`GRPOTrainer(environment_factory=…)`；
- OpenRLHF、AReaL、slime、ART。

2026 年的条目在我的知识截止日期（2026-06）之后，引用前我会再读原文。

## C. 需要你决定的事

1. **门禁放宽（S2）**：原始代码上以相同签名失败的测试，是否在驳回一次之后就放行？
2. **单次运行的记忆维护（S3）**：是否在单次运行中默认关闭？
3. **先做哪部分**：先做 A（降成本，回答“为什么请求多”）还是先做 B（打通训练闭环）。我的建议是先做 A 的 S1、S4、S5 和 B 的 R1–R3：前者在 fresh30b 上有望拿到显著的成本下降，后者不占 GPU。等 GPU 空出来再做 R4–R7。
4. **GPU**：两张卡目前被其他任务占用，RL 阶段需要你安排出使用时间。
