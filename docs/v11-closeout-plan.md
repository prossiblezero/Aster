# v11 收尾实施方案（2026-10-16）

本文档写给实施代码的 Agent（Codex）。v11 两轮评测已经完成（`evaluations/turn-reduction-20261015/`），结论是：**保留 Stage 1、2，撤掉 Stage 3**；修复评测中暴露的一个存档 bug；最后把整个降本工作的结果汇总成一份报告。本方案**不调用模型、不做付费评测**。

## 0. 使用规则

1. 按 Stage A → C 的顺序实施，每个 Stage 先通过自己的“验收”，再进入下一个。
2. 第 5 节“不要改”优先于其他任何内容。
3. 不新增依赖、配置项、模型调用或服务。
4. 测试命令（项目环境 `/home/dzh/.conda/envs/aster/bin/python`）：

```bash
python -m ruff check src tests
python -m pytest -q -m core
python -m pytest -q -m "not core"
```

### 0.1 Git 规则

- 继续在分支 `perf/turn-reduction` 上工作。
- 每个 Stage 验收通过后单独提交一次，并打标签（标签名见各 Stage 末尾）。
- 提交前运行 `git status --porcelain` 逐项确认；不要提交 `.env`、`.env.*`（`.env.example` 除外）和 `artifacts/private/`，不要用 `git add -f`。
- 对暂存内容运行 `git diff --cached | grep -nE "sk-[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9]|api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}"`，必须没有输出。注意：评测 JSON 里若出现模型推理密文，可能误中第一条规则；遇到时逐条确认是密文而不是密钥，并在提交说明中写明。
- 只做本地提交，不 push；不改写或删除已有提交和标签；不使用 `git reset --hard`、`git push --force`、`git clean`。

## 1. 背景（只需了解，不需实施）

| 版本 | old20 首次独立通过 | 全部请求 | 加权成本* |
| --- | ---: | ---: | ---: |
| 优化前 Aster（`c6b651d`，50 题评测中的 old20） | 15/20 | 405 | 7.16 |
| v11 Stage 1+2（第一轮 attempt 1） | 14/20 | 339 | 3.10 |
| pi（历史） | 14/20 | 240 | 2.27 |
| Codex（历史） | 15/20 | 254 | 2.34 |

\* 加权成本 =（未缓存输入 × 1.25 + 缓存输入 × 0.125 + 输出 × 10）/ 1e6，含后台记忆请求，只作相对比较，不是账单。

第二轮（加 Stage 3）的质量门槛通过，但：

- 首次 20 题中有 46 次请求异常，第一轮只有 3 次；4 道题因 `provider_error` 提前结束。剔除这 4 道题后，模型轮数 252 → 230，请求数 279 → 281，降本效果测不出来。
- Stage 3 针对的 `psf__requests-2931`，带失败轮数从 3 涨到 9。
- `pytest-dev__pytest-10356` 中，模型把一条与任务直接相关的测试声明为 pre-existing，运行时接受了，靠人工复核才否决。这说明“基线上也失败”不足以证明“与任务无关”。

所以撤掉 Stage 3。它的代码保留在标签 `v11-stage3` 上，可以随时取回。

## 2. Stage A：记录第二轮结果并撤掉 Stage 3

1. **先提交第二轮结果**，原样提交 Codex 已生成的文件：`REPORT-round2.md`、`audit-round2.json`、`audit-notes-round2.md`、`preexisting-review-round2.json`、`round2/`。提交信息 `eval(turn-reduction): record round 2 results (Stage 1+2+3)`，正文写明质量门槛通过、三个未达标项及其原因。
2. **只撤销源码和测试，保留评测记录**。标签 `v11-stage3` 指向的提交 `8290388` 同时改了 `src/`、`tests/` 和两个实施记录文件（`IMPLEMENTATION.md`、`stage3-checks.json`）。记录文件之后又被修改过，直接 revert 会在 `IMPLEMENTATION.md` 上冲突，而且会删除 `stage3-checks.json`。这些记录是 Stage 3 做过的事实，应该保留。按以下顺序执行（已在临时工作树中预演）：

   ```bash
   git revert --no-commit 8290388
   git checkout HEAD -- evaluations/turn-reduction-20261015/
   git status --porcelain        # 只应剩下 src/ 和 tests/ 的改动，没有 UU
   git diff --cached v11-stage2 -- src tests   # 必须没有输出
   git commit
   ```

   如果 `src/` 或 `tests/` 中出现冲突，**停下报告**，不要手工合并。
3. 提交信息为 `revert(verification): withdraw baseline check and pre-existing declarations`，正文写明：
   - 撤销原因，即第 1 节最后三点；
   - 代码保留在标签 `v11-stage3`；
   - 评测记录文件有意未撤销。
4. 在 `evaluations/turn-reduction-20261015/IMPLEMENTATION.md` 末尾追加一节“Stage 3 撤销”，写明撤销提交的哈希和原因。这一改动单独提交：`docs(eval): note Stage 3 withdrawal`。

**验收**：

- `git diff v11-stage2 HEAD -- src tests` 没有输出，说明源码和测试与第一轮实际评测的版本完全一致。
- 全量测试结果与 `evaluations/turn-reduction-20261015/stage2-*.log` 中的 Stage 2 结果一致，没有新增失败。

验收通过后打标签 `v11-final`。

## 3. Stage B：修复请求存档误伤推理密文

### 3.1 问题

第二轮审计中，`terminal-cobol-modernization` attempt 2 有 5 个前台请求与归档快照对不上（`audit-notes-round2.md`）。原因：

- `Runtime.prepare_request`（`src/aster/core/runtime.py`）用 `self.evidence.save(artifact, {"policy_version": ..., "payload": payload})` 保存请求快照。
- `EvidenceStore.save`（`src/aster/storage/evidence.py`）对整个数据调用 `self.redactor.value(data, mask_fields=False)`。
- `Redactor.text`（`src/aster/safety/redaction.py`）的规则 `\bsk-[a-z0-9_-]{12,}` 会命中推理项 `encrypted_content` 中恰好出现的 `sk-...` 片段，把它替换成 `[REDACTED]`。

发给模型的请求不受影响，只是存档副本与实际请求不一致，导致审计报错。`EvidenceStore.redact_history` 已经有“先脱敏、再把协议密文字段原样还原”的逻辑（`_native_opaque_fields`），但 `save` 只在 `preserve_native_history=True` 时对 `structured_history` 键使用它，请求快照没有用上。

### 3.2 改动

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `EvidenceStore.save` | 参数 `preserve_native_history: bool`，只处理 `structured_history` 键 | 增加关键字参数 `native_history_paths: tuple[tuple[str, ...], ...] = ()`。对每个路径，例如 `("payload", "input")`，取出原数据中该位置的列表，用 `self.redact_history(...)` 处理后写回脱敏结果的同一位置。`preserve_native_history=True` 等价于 `native_history_paths=(("structured_history",),)`，现有调用保持不变 |
| `Runtime.prepare_request` 保存请求快照处 | `self.evidence.save(artifact, {...})` | 加 `native_history_paths=(("payload", "input"),)`。只对 Responses 协议的请求体有效；Chat、Anthropic 的请求体没有 `input` 键时跳过 |

`redact_history` 内部会先校验 `validate_native_history`，再检查控制字段中有没有凭证。请求的 `input` 本来就是 `render_prompt` 经 `redact_history` 处理过的结果，所以这里不会改变任何非密文内容。

另外请检查 `EvidenceStore.emit` 写入的事件里有没有包含推理项 `encrypted_content` 的字段，例如 `model_reply` 事件的原始回复。有的话，按同样方式处理，并在提交说明中列出。

### 3.3 测试

新增到 `tests/test_history_gateway_metadata.py` 或新文件 `tests/test_request_archive.py`：

1. 构造一个推理项，`encrypted_content` 中含 `"xxsk-" + "abcdefghijklmnop"` 拼接得到的片段，经 `prepare_request` 保存后，读回的请求快照 `payload.input` 与实际请求体逐字节相等，`request_sha256` 与保存内容的哈希一致。
2. 同一请求中，用户消息或工具输出里真正出现的 `sk-...` 密钥仍被替换为 `[REDACTED]`。
3. 现有 `save(..., preserve_native_history=True)` 的检查点测试不经修改通过。

另外，在 `artifacts/private/turn-reduction-20261015/round2/attempt2/runs/aster-terminal-cobol-modernization/` 上离线验证：对 5 个出错请求的出站请求体调用修复后的保存逻辑，结果应与出站内容一致。只读，不修改原始证据，也不重算第二轮的冻结审计。

**验收**：测试通过；全量测试相对 Stage A 没有新增失败。提交 `fix(evidence): keep native reasoning ciphertext exact in request archives`，打标签 `v11-final-fix1`。

## 4. Stage C：降本工作结果汇总

新建 `evaluations/cost-summary-20261016.md`，作为 2026-10-12 以来整个降本工作的总结报告。读者是项目作者本人，用于对外介绍和面试准备，所以要写得简洁，数字可以溯源。

内容：

1. **一张主表**：old20、同模型 `gpt-5.6-luna`、同运行预算下，优化前 Aster、v9、v10、v11 Stage 1+2、pi、Codex 的以下指标：
   - 首次独立通过；
   - 全部请求数；
   - 输入 token、缓存输入、未缓存输入、输出；
   - 缓存占比；
   - 加权成本。

   每行注明数据来源文件。数据来源：

   | 行 | 来源 |
   | --- | --- |
   | 优化前 Aster、pi、Codex | `evaluations/general-coding-50-20261012/results.json` 中 old20 的 20 题，含后台请求 |
   | v9 | `evaluations/context-cache-20261013/summary.json` |
   | v10 | `evaluations/context-state-20261014/summary.json` |
   | v11 Stage 1+2 | `evaluations/turn-reduction-20261015/comparison-round1.json` 的 `per_attempt_spend[0]` |

2. **每一步改了什么、解决了哪个问题**，每步一段话，引用对应的方案文档和评测报告：

   | 版本 | 根因 | 改法 | 机制指标的变化 |
   | --- | --- | --- | --- |
   | v9 | 裁剪改写已发送的历史；本地计量偏高 1.78 倍；不发缓存键 | 缓存键、回执计量、只追加历史和观测掩码 | 改写 146 → 2 |
   | v10 | 每轮替换的尾部消息跨过缓存块边界 | 状态变化时追加上下文更新条目 | 缓存占比 72% → 89%；附付费探针结论 |
   | v11 | 自己的编辑也要求重读；搜索只支持字面量 | 自身编辑保留读取凭证、正则搜索 | 被拦编辑 11 → 0，轮数 358 → 304 |

3. **质量**：14 道稳定题、6 道波动题的口径，以及为什么单次 20 题不能判断 ±2 题的差别。数据见 `docs/turn-reduction-plan.md` 第 1.3 节。
4. **试过但撤掉的**：Stage 3 及撤掉原因（第 1 节）。
5. **限制**：
   - 20 题是用于开发的子集，不是未见测试集；
   - 单次运行；
   - 加权成本不是账单；
   - 与 pi、Codex 的对比使用它们的历史运行；
   - 供应商故障率在不同评测间差异很大。
6. **剩余差距**：v11 的请求数仍是 pi 的约 1.4 倍，原因见 `docs/turn-reduction-plan.md` 第 8 节。

**验收**：表中每个数字都能用文中给出的来源文件和计算方法复算；用一个只读脚本 `evaluations/cost-summary-20261016.py` 生成主表，在 md 中写明运行命令。提交 `docs(eval): summarize cost reduction from c6b651d to v11`，打标签 `v11-summary`。

## 5. 不要改

- `v11-stage2` 中 Stage 1、2 的实现。
- v9/v10 的上下文策略和 `policy_version`。
- 任何冻结的评测协议、审计脚本、原始证据和既有报告中的数字。
- `Redactor` 的匹配规则本身。本次只让请求存档不对协议密文字段做脱敏，不改密钥识别规则。
- 已有提交和标签。

## 6. 后续方向（不在本次实施）

按性价比从高到低排序，供用户决定是否继续：

1. **沙箱文件属主显示为 `UNKNOWN` 导致 `Permission denied`**：第一轮解释器题、v9 的 gRPC 题都遇到过。`read_file` 能读，`run_shell` 却无法执行 `/app/interp.py`。这会随机打断终端题，放大评测噪声。修好后评测结果会更稳定。
2. **读取粒度**：`read_file` 默认 200 行，模型请求的中位数为 71 行，pi 为 110 行。可以在工具描述中鼓励一次读足够大的范围，或者调整默认值。这会改变 Agent 行为，需要按 v11 的评测方法验收。
3. **重新设计 pre-existing 声明**：只允许声明错误类型属于网络、权限、缺依赖等环境问题的失败，并且要求这些测试不在本次修改涉及的文件中。
