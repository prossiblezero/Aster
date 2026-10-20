# 减少模型轮次实施方案（v11，2026-10-15）

本文档写给实施代码的 Agent（Codex）。v10（[上下文状态方案](context-state-plan.md)）之后，Aster 每个请求的未缓存输入和输出已经和 pi、Codex 持平。剩下的成本差距几乎全部来自模型轮次多。本方案去掉三处已查明的轮次浪费，并按可回滚的方式逐个提交。

## 0. 使用规则

1. 按 Stage 0 → 5 的顺序实施。每个 Stage 先通过自己的“验收”，再进入下一个。
2. 第 9 节“不要改”优先于其他任何内容。
3. 行号基于当前工作区（`0ce78db` + v9 + v10 未提交改动），定位以**函数名**为准。
4. 只报告相对 Stage 0 基线的测试变化，不要声称修好了基线里的失败。
5. Stage 5 会调用模型、消耗额度。准备好协议和脚本后**停下，等用户确认**再运行。两轮评测各需单独确认。
6. 不新增依赖、配置项或服务。

### 0.1 Git 规则（必须遵守，用于回滚）

- 所有工作在新分支 `perf/turn-reduction` 上进行，从当前 `feat/subagent-light` 的工作区创建。
- **每个 Stage 结束且验收通过后，单独提交一次，并打标签**（标签名见各 Stage 末尾）。不要把两个 Stage 合进一个提交。
- 每个提交只包含该 Stage 的源码、测试、文档和评测脚本。提交信息格式为 `<type>(<scope>): <摘要>`，正文列出改动的函数和修改过的既有断言。
- 提交前必须：
  1. 运行 `git status --porcelain`，逐项确认要提交的文件。
  2. 不要提交 `.env`、`.env.*`（`.env.example` 除外）、`artifacts/private/` 下的任何内容。它们已在 `.gitignore` 中，不要用 `git add -f` 绕过。
  3. 对暂存内容运行 `git diff --cached | grep -nE "sk-[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9]|api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}"`，必须没有输出。
- 只做本地提交，**不要 push**，不要改写或删除已有提交和标签，不要 `git reset --hard`、`git push --force`、`git clean`。
- 回滚方式写进每个 Stage 的验收记录：`git revert <该 Stage 的提交>`，或 `git checkout <上一个标签>` 后新建分支。

测试命令（项目环境 `/home/dzh/.conda/envs/aster/bin/python`）：

```bash
python -m ruff check src tests
python -m pytest -q -m core
python -m pytest -q -m "not core"
```

## 1. 问题与证据

数据来自 v10 的 old20 单次评测（`evaluations/context-state-20261014/`，原始请求在 `artifacts/private/context-state-20261014/runs/`），pi、Codex 来自 50 题评测中相同的 20 题。分析脚本在 `evaluations/cache-probe-20261014/`，都只读归档、不调用模型。

### 1.1 成本差距来自轮次

| | 有用量的前台请求 | 平均输入 | 未缓存输入/请求 | 输出/请求 | 加权成本* |
| --- | ---: | ---: | ---: | ---: | ---: |
| Aster v10 | 378 | 25,506 | 2,924 | 284 | 3.52 |
| pi | 238 | 20,423 | 3,377 | 316 | 2.27 |
| Codex | 249 | 24,635 | 2,818 | 316 | 2.34 |

\* 加权成本按未缓存输入 1.25、缓存输入 0.125、输出 10（每百万 token）估算，只用于比较相对大小，不是实际账单。

单个请求的开销已经持平，成本比和请求数比都约为 1.5 倍。

### 1.2 多出的轮次在哪里

`turn_kinds.py` 按每轮调用的用途分类（Aster 358 轮，pi 213 轮）：

| 轮次类型 | Aster | pi | 差值 |
| --- | ---: | ---: | ---: |
| 纯探索（只读文件、搜索、列目录） | 114 | 40 | +74 |
| 编辑 | 85 | 62 | +23 |
| 其他 shell | 70 | 52 | +18 |
| 测试 | 68 | 57 | +11 |
| 看 diff | 11 | 2 | +9 |
| 无工具（回答） | 10 | 0 | +10 |

已查明的三个来源：

**A. 编辑后强制重读。** `Runtime._execute`（`core/runtime.py`）要求 `write_file`/`patch_file` 的目标文件在 `state.read_evidence` 中的哈希与当前内容一致。工具自己的成功编辑也会让哈希失效（`_refresh_observations` 发现不一致后删除凭证），所以对同一文件的每次后续编辑都必须先 `read_file`。`reread_audit.py` 和 `turn_kinds.py` 统计：

- 39 次 `read_file` 读的是自己刚编辑过的文件；
- 27 轮**只**在重读自己刚编辑过的文件；
- 11 轮的编辑整轮被 `fresh_read_required` 拦下。

pi 的 `edit` 和 Codex 的 `apply_patch` 都没有这个门禁，它们靠字面匹配 `old_text` 来保证编辑安全，而 Aster 的 `patch_file` 本身也要求 `old_text` 唯一匹配。

**B. `search` 只支持字面子串。** `WorkspaceTools._search`（`tools/workspace.py`）使用 `pattern in line.casefold()`，不支持正则，`a|b` 会被当作字面量。v10 共调用 102 次 `search`，21 轮只做搜索或列目录。pi 用 `rg -n "a|b|c"` 一次搜索多个模式。

**C. 追与任务无关的测试失败。** `Verifier.observe`（`core/verifier.py`）把 `run_tests` 中失败的测试记入 `state.verification_failures`，`completion_feedback`（`core/recovery.py`）在其清空前拒绝完成。系统提示词要求“call them pre-existing only with evidence from unchanged baseline code”，但 Aster 没有任何在未修改代码上运行测试的手段。`failure_chase.py` 统计：

- 358 轮中有 60 轮是在存在未解决失败时进行的，完成门禁共拦截 10 次；
- `psf__requests-2931` 和 `psf__requests-5414` 共约 22 轮在追 `TestTimeout::test_connect_timeout` 等测试，期间两次修改 vendored 的 `requests/packages/urllib3/connection.py`。这些测试需要外网，在沙箱中本来就失败。

### 1.3 通过率的现状（决定评测方法）

这 20 题 Aster 共评测过 6 次：

| 评测 | gc1010 | opt1011 | optv2 | gc50 | v9 | v10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 通过 | 12 | 14 | 15 | 15 | 13 | 13 |

**稳定题**：6 次结果完全一致的有 14 题。

- 恒过 10 题：`django__django-17029`、`sympy__sympy-23262`、`sphinx-doc__sphinx-9230`、`pydata__xarray-7233`、`pydata__xarray-3677`、`pytest-dev__pytest-7571`、`psf__requests-2931`、`terminal-polyglot-c-py`、`aider-simple-linked-list`、`aider-sgf-parsing`。
- 恒败 4 题：`django__django-11400`、`sympy__sympy-17630`、`sphinx-doc__sphinx-11510`、`pytest-dev__pytest-10356`。

**波动题**：6 题，括号内为 6 次中的通过次数，合计 21/36。

- `terminal-cancel-async-tasks`（2）
- `terminal-custom-memory-heap-crash`（4）
- `terminal-schemelike-metacircular-eval`（4，每次都跑满 900 秒超时）
- `terminal-kv-store-grpc`（2）
- `terminal-cobol-modernization`（5）
- `psf__requests-5414`（5）

按这些频率计算，一个不变的 Aster 单次跑 20 题，期望通过 13.7 题，得到 ≥15 题的概率只有 22%。所以本方案**不用单次 ≥15/20 作为质量门槛**，改用第 7 节的方法：14 道稳定题检查是否翻转，6 道波动题重复运行后比较通过频率。

## 2. Stage 0：分支、基线与离线指标

不改生产代码，不调用模型。

1. 按 0.1 节创建分支 `perf/turn-reduction`。
2. **先把当前状态保存为基线提交**，分两次提交：
   - 提交一：`src/`、`tests/`、`scripts/` 中的改动，即 v9 + v10 的实现。提交信息 `feat(context): receipt metering, append-only history and context-state notes (v9+v10)`。
   - 提交二：其余已修改和未跟踪的 `docs/`、`evaluations/`、`datasets/`、`learn_web/`、`README.md` 等。提交信息 `docs(eval): record v9/v10 evaluations and plans`。
   - 打标签 `v10-baseline`。
3. 运行全量测试，保存到 `evaluations/turn-reduction-20261015/baseline-*.log`，作为之后所有比较的基线。
4. **新建** `evaluations/turn-reduction-20261015/turn_metrics.py`：合并 `evaluations/cache-probe-20261014/` 中 `turn_kinds.py`、`reread_audit.py`、`failure_chase.py` 的逻辑，对一个 runs 目录输出一个 JSON：

   | 字段 | 含义 |
   | --- | --- |
   | `turns` | 最终转录中的模型轮次 |
   | `turn_kinds` | 按 1.2 节分类的轮次数 |
   | `reread_after_own_edit` | 读自己编辑后未再读过的文件的 `read_file` 次数 |
   | `reread_only_turns` | 只做上述重读的轮次 |
   | `fresh_read_blocked` | 结果为 `fresh_read_required` 的编辑调用数 |
   | `search_calls`、`search_only_turns` | 搜索调用数；只含 `search`/`list_files` 的轮次 |
   | `turns_while_failing` | 存在未解决验证失败（以最近一条 `workflow_state` 条目为准）时的轮次 |
   | `completion_gates` | 以 `Completion requirements missing` 开头的用户消息数 |
   | `preexisting_declared` | Stage 3 之后：被接受的 pre-existing 声明数 |

**验收**：对 v10 运行 `python evaluations/turn-reduction-20261015/turn_metrics.py artifacts/private/context-state-20261014/runs`，结果必须是：`turns` 358，`turn_kinds.discover_only` 114，`reread_after_own_edit` 39，`reread_only_turns` 27，`fresh_read_blocked` 11，`search_calls` 102，`search_only_turns` 21，`turns_while_failing` 60，`completion_gates` 10。

基线提交在第 2 步完成，后续 Stage 不需要再打 Stage 0 标签。

## 3. Stage 1：自己的成功编辑保持读取凭证

**目标不变量**：Aster 通过 `write_file`/`patch_file` 成功写入某文件后，只要磁盘上的内容仍是它写入的内容，就可以继续编辑该文件，不需要重读。其他途径造成的变化（`run_shell`、Worker、用户、外部进程）仍然必须重读。

### 3.1 改动

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `tools/workspace.py` `_write_file` | 成功后 `result.data["path"] = args.path` | 成功时另加 `result.data["written_sha256"]`：实际写入磁盘的**字节**的 sha256（见 3.2） |
| `tools/workspace.py` `_patch_file` | 返回 `sandbox.run` 的结果 | 成功时同样加 `written_sha256`（`new_text` 按写入编码得到的字节的 sha256）和 `path` |
| `core/runtime.py` `_collect_step` | 编辑成功后，凭证随后由 `_refresh_observations` 删除 | `outcome.status == "success"`、`call.name in {"write_file", "patch_file"}`，且 `outcome.data` 含 `written_sha256` 时：重新读取该文件，用门禁的同一表达式计算当前哈希；与 `written_sha256` **相等**才设置 `state.read_evidence[path] = 当前哈希`，否则不设置。这一步必须在本方法调用 `_refresh_observations()` 之前完成 |
| `core/runtime.py` `_execute` 中 `fresh_read_required` 的提示文本 | “Every successful edit invalidates the previous read, including edits in the same batch.” | 改为：“The file changed since your last read_file or your own last edit. Call read_file on {path} before retrying.” |
| `patch_file` 工具描述（`tools/schemas.py` 第 236 行附近的描述字符串） | “Call read_file before editing; every successful edit invalidates that read.” | 改为：“Call read_file before the first edit. Your own successful write_file/patch_file edits keep the file current; changes made any other way require a fresh read_file.” |

`state.working_memory.pop(path)`、`changed_paths`、验证状态失效等现有逻辑不变。

### 3.2 哈希一致性

门禁比较的是 `hashlib.sha256(current.encode()).hexdigest()`，其中 `current` 来自 `read_edit_source(target)`。`written_sha256` 必须与之可比：

`_collect_step` 中按以下顺序处理：

1. 读取磁盘上该文件的原始字节，计算 sha256，与 `outcome.data["written_sha256"]` 比较。不相等说明写入后文件又被改过，直接跳过，不设置凭证。
2. 相等时，用门禁的同一表达式（`read_edit_source(target)` 后 `hashlib.sha256(current.encode()).hexdigest()`）计算 `h_gate`，设置 `state.read_evidence[path] = h_gate`。

不要用 `read_file` 的 `digest(text)` 替代 `h_gate`，以免换行和 BOM 的规范化方式与门禁不一致。

### 3.3 测试

修改 `tests/test_fresh_read.py`，新增：

1. 首次编辑仍要求先读（原测试前半段不变）。
2. 读一次后连续两次 `patch_file` 同一文件，第二次**成功**，中间不调用 `read_file`。
3. 自己编辑后，用 `path.write_text(...)` 模拟外部修改，再 `patch_file` 返回 `fresh_read_required`。
4. 自己编辑后，通过 `run_shell` 修改同一文件，再 `patch_file` 返回 `fresh_read_required`。
5. `write_file` 新建文件后，立即 `patch_file` 该文件成功。
6. 同一批中对同一文件的两个不重叠 `patch_file` 都成功（两者的 `old_text` 在原文件中唯一，且第一处编辑后第二处仍唯一）。

`tests/test_write_approval_race.py`、`tests/test_tool_parallelism.py` 中所有 `fresh_read_required` 断言（外部修改场景）必须**不经修改**通过。其余列出 `fresh_read_required` 的测试文件（`test_batch_patch_output.py`、`test_session_continuation.py`、`test_context_archives.py`、`test_worker_handoff.py`、`test_dependency_environment.py`、`test_interaction_workers.py`）若因“编辑后不再失效”而失败，逐条判断：只有断言内容是“自己编辑后必须重读”的，才改为新行为，并在提交说明里列出。

**验收**：上述测试通过；全量测试相对基线无新增失败。提交 `perf(tools): keep read evidence after own successful edits`，打标签 `v11-stage1`。

## 4. Stage 2：`search` 支持正则

| 位置 | 当前 | 改为 |
| --- | --- | --- |
| `tools/schemas.py` `Search` 模型 | `pattern` 为字面子串；描述写明不是正则 | 增加 `regex: bool = False`。描述改为：“Case-insensitive. With regex=false (default) pattern is a literal substring; with regex=true it is a Python regular expression, e.g. 'def (foo\|bar)\\b'.” `pattern` 增加 `max_length=300` |
| `tools/workspace.py` `_search` | `pattern in line.casefold()` | `regex` 为真时 `re.compile(args.pattern, re.IGNORECASE)`，对 `line[:2000]` 调用 `.search`；编译失败返回 `Outcome(status="blocked", reason="invalid_regex", content=<错误信息>)`。`regex` 为假时行为完全不变 |
| `search` 工具描述（`tools/schemas.py` 第 226 行附近） | “Search workspace files for a case-insensitive literal substring, not a regex.” | 改为：“Search workspace files line by line, case-insensitively. Use regex=true for alternation or patterns (one call can cover several names).” |

只匹配每行前 2000 字符，用来限制病态正则的开销；输出仍然每行最多 300 字符。

**测试**（新增到 `tests/test_tool_pagination.py` 或新文件）：

- `regex=true` 时 `foo|bar` 能匹配两种行，`regex=false` 时只匹配字面量 `foo|bar`。
- 无效正则返回 `invalid_regex`。
- 现有 `search` 分页测试不经修改通过。

**验收**：测试通过；全量测试无新增失败。提交 `perf(tools): add regex search`，打标签 `v11-stage2`。

## 5. Stage 3：基线复跑与 pre-existing 声明

**目标**：模型能拿到“这条失败在未修改代码上也存在”的证据，并能用一次显式声明把它从完成门禁中移出。运行时只接受有基线证据的声明。**不自动豁免任何失败**：像 aider 练习题这样，仓库自带的测试在实现完成前本来就失败，自动豁免会让门禁失效。

### 5.1 基线复跑

- **何时运行**：`run_tests` 结果中存在失败测试 id 不在 `state.baseline_results` 中，且本次调用没有 `code`（内联测试）时，在**同一次工具调用内**再运行一次基线。不额外消耗模型轮次。
- **基线副本**：从当前工作区复制，然后：
  - 对 `Runtime._initial_text` 中内容与当前不同的文件，写回初始文本；
  - 删除当前存在、`_initial_text` 中没有的文本文件；
  - 恢复 `_initial_text` 中有、当前已删除的文件；
  - 其他文件保持不变。
- **怎么跑**：使用相同的命令、相同的超时，复用 `run_tests` 的报告解析。
- **记录**：对本次每个失败 id，记 `state.baseline_results[id]`：
  - `"failed"`：基线上同样失败；
  - `"passed"`：基线通过，即本次修改引入的失败；
  - `"missing"`：基线中未收集到，例如新增的测试。
- **参数传递**：`run_tests` 函数增加关键字参数 `baseline: dict[str, str] | None` 和 `checked: set[str]`，由 `WorkspaceTools._run_tests` 从 runtime 上下文传入。若需要访问 `_initial_text`，经工具上下文对象传递，不要让 `tools` 层导入 `core`。
- **追加到结果**：在 `outcome.content` 末尾追加一段：

  ```text
  Baseline check (same command on the unmodified task files): <k> of <n> failing tests also fail there: <最多 10 个 id>. If the task does not require them to pass, declare them with run_tests preexisting=[...]; they will be reported as pre-existing, not as fixed.
  ```

  `k == 0` 时只写 “Baseline check: none of the failing tests fail on the unmodified task files.”
- **失败时**：基线运行超时或出错时，追加 “Baseline check unavailable: <reason>”，不写入 `baseline_results`。

### 5.2 声明

| 位置 | 改动 |
| --- | --- |
| `tools/schemas.py` `RunTests` | 增加 `preexisting: list[str] = Field(default_factory=list, max_length=20)`，描述：“Failing test ids that the latest baseline check showed also fail on the unmodified task files and that the task does not require to pass.” |
| `schema.py` `RunState` | 增加 `baseline_results: dict[str, str]`、`preexisting_failures: dict[str, str]`（id → 命令），默认空字典。检查点、恢复、分叉、`training/env.py` 快照随 `RunState` 序列化，不需要额外处理 |
| `core/verifier.py` `Verifier.observe` | 处理本次 `preexisting` 中的每个 id：`baseline_results.get(id) == "failed"` 时，从 `verification_failures` 移入 `preexisting_failures`；否则不接受。之后遇到 `preexisting_failures` 中的 id 失败，不再写入 `verification_failures`；若它变为通过，从 `preexisting_failures` 中删除 |
| `run_tests` 结果 | 存在声明时追加：“Accepted as pre-existing: [...]. Not accepted (no baseline failure on record): [...]” |
| `context/prompt.py` `workflow_state` | `preexisting_failures` 非空时增加 `"preexisting_failures": dict(list(...)[:8])` 与 `"preexisting_failure_count"` |
| `core/verifier.py` `Verifier.final` | 通过时在 `details` 中加入 `"preexisting_failures"`，使最终报告列出它们 |
| `context/prompt.py` `system_prompt` | 把 “Resolve observed test failures; call them pre-existing only with evidence from unchanged baseline code.” 改为：“Resolve observed test failures. run_tests automatically checks new failures against the unmodified task files; declare a failure pre-existing only when that baseline check shows it and the task does not require it to pass.” |

`completion_feedback` 的文字不变。被接受的 id 已不在 `verification_failures` 中，所以门禁自然放行。

### 5.3 测试

新建 `tests/test_baseline_failures.py`：

1. 仓库自带一个基线就失败的测试；Agent 修改无关文件后运行测试，得到基线证据 `"failed"`；声明后完成门禁放行；`Verifier.final` 的 `details` 列出该测试。
2. Agent 的修改**引入**了失败（基线通过）：证据为 `"passed"`，声明被拒绝，门禁仍拦截。
3. 新增的测试文件失败：证据为 `"missing"`，声明被拒绝。
4. 已检查过的失败再次失败，不重复运行基线（用计数桩验证）。
5. 内联 `code` 测试不运行基线。
6. 基线运行超时：结果写明 unavailable，不写入 `baseline_results`。
7. 恢复后 `baseline_results`、`preexisting_failures` 保留。

**验收**：测试通过；全量测试无新增失败。提交 `feat(verification): baseline check and explicit pre-existing failures`，打标签 `v11-stage3`。

## 6. Stage 4：评测准备（不调用模型）

新建 `evaluations/turn-reduction-20261015/`：

1. 复制 `evaluations/context-state-20261014/` 的 `evaluate.py`、`audit.py`、`metrics.py`，协议条件与 v10 完全相同：`gpt-5.6-luna`、Responses、medium、64000 上下文、8192 输出上限、900 秒/题、4 CPU / 8 GiB、6 槽并发、空会话、`max_workers=0`、自动记忆开启。
2. `evaluate.py` 增加 `--tasks` 和 `--attempt` 参数，支持对指定题目追加第 2、3 次尝试。每次尝试的原始数据存在独立目录，**不得覆盖**。
3. 写 `PROTOCOL.md`，内容为第 7 节的两轮计划和门槛，冻结后不得修改。
4. 停下，等用户确认第一轮。

## 7. Stage 5：两轮付费评测

### 7.1 第一轮：Stage 1 + 2（标签 `v11-stage2`）

在 `git checkout v11-stage2` 的状态下运行，Stage 3 的代码不参与。

- old20 全部 20 题各 1 次。
- 6 道波动题各再跑 2 次，每题共 3 次。

合计 32 次任务尝试。

| 类别 | 指标 | v10 | 要求 |
| --- | --- | ---: | ---: |
| 质量 | 恒过 10 题 | 10/10 | 10/10。若有 1 题失败，对该题追加 2 次，2 次都过才算通过 |
| 质量 | 恒败 4 题 | 0/4 | 只报告 |
| 质量 | 波动 6 题 × 3 次 | 历史频率 21/36 | 通过 ≥ 8/18（历史期望 10.5） |
| 机制 | `fresh_read_blocked` | 11 | ≤ 2 |
| 机制 | `reread_only_turns` | 27 | ≤ 5 |
| 机制 | `search_only_turns` | 21 | 报告 |
| 成本 | old20 单次的 `turns` | 358 | ≤ 325 |
| 成本 | old20 单次的有用量前台请求 | 378 | 报告 |
| 缓存 | `cache_ratio`、`strict_rewrites` | 0.885、4/358 | ≥ 0.85、≤ 3% |
| 成本 | 1.1 节的加权成本 | 3.52 | 报告 |

成本指标只统计 old20 的第 1 次尝试，以便与 v10 对比；追加尝试只用于质量判断。

第一轮未通过时，停止并报告，不进入第二轮。是否回滚到 `v10-baseline`（`git revert` Stage 1、2 的提交）由用户决定。

### 7.2 第二轮：Stage 1 + 2 + 3（标签 `v11-stage3`）

第一轮通过、用户再次确认后运行，规模与第一轮相同。

| 类别 | 指标 | 第一轮结果 | 要求 |
| --- | --- | ---: | ---: |
| 质量 | 同 7.1 的三项 | — | 同 7.1 |
| 质量 | `aider-simple-linked-list`、`aider-sgf-parsing` 中被接受的 pre-existing 声明 | — | 0（这两题的测试是任务本身要求的） |
| 机制 | `psf__requests-2931` + `psf__requests-5414` 的 `turns_while_failing` | v10 为 22 | ≤ 8 |
| 机制 | 所有被接受的声明 | — | 逐条列出 id 与基线证据，人工检查是否与任务无关 |
| 成本 | old20 单次的 `turns` | 第一轮值 | 不高于第一轮值 |

第二轮质量未通过时，回滚 Stage 3：`git revert <v11-stage3 的提交>`，保留 Stage 1、2。

### 7.3 报告

每轮写 `REPORT-round<N>.md`，包含：

- 上述门槛表；
- 稳定题逐题结果、波动题每次尝试的结果；
- `turn_metrics.py` 输出与 v10 的对比；
- 全部请求异常；
- 当前提交与标签。

## 8. 不在本方案范围

以下原因也会增加轮次或上下文，但本次不改，以免一次引入过多行为变化：

- `read_file` 的读取范围偏小：模型请求的中位数为 71 行，pi 为 110 行；分段读 35 次，重复读 26 次。
- 首次读的文件更多：87 个，pi 为 76 个。这是探索习惯问题，需要提示词层面的单独设计。
- 工具输出的 JSON 信封约占工具输出 token 的 28%，包括 64 位十六进制的 `evidence_id`、`source_sha256` 和 JSON 转义。这部分大多已被缓存，对成本的影响较小。
- 完成门禁中的 “run a successful verification command via run_tests” 分支（终端题 5 次）。

## 9. 不要改

- v9/v10 的上下文策略：缓存键、回执计量、只追加历史、压缩、上下文更新条目。`policy_version` 保持 `deterministic-v10`。
- 除 5.2 指定的那一句外，系统提示词的其他文本。
- 门禁对外部修改的拦截：`test_write_approval_race.py`、`test_tool_parallelism.py` 中相关断言必须不经修改通过。
- `patch_file` 的唯一匹配、重叠检查、BOM 与换行处理。
- `completion_feedback` 的文字、`Verifier.final` 的其他判定顺序、`require_verification` 的推断。
- Worker 的写入锁、`source_excerpts`、`state.working_memory`。
- 已有提交、标签和 `artifacts/private/` 中的原始评测数据。
