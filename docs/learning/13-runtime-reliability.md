# 13. 让长任务可以继续，也能正确停下

先读 [评测入门](12-evaluation.md)。本阶段讨论的不是“换一个更聪明的模型”，而是同一个模型发出合法动作时，Harness 能否稳定执行并保留证据。配套问题与验收条件见 [修复计划](../repair-plan.md)。

## 问题：连续修改三个文件，为什么会被认为在原地打转？

很多写文件命令成功后不会输出文字。如果停滞检测只比较工具输出，连续三次成功修改都会得到空字符串，便可能误判为重复失败。现在 [StallDetector.observe](../../src/aster/recovery.py) 先看 `Outcome.changed_paths`：有真实文件变化就清空旧窗口；没有变化时，才比较工具名称、参数、状态、原因和内容。

这里的 `repeated_identical` 表示连续重复相同动作与结果；`oscillation` 表示在两个不同动作之间 ABAB 来回；`output_loop` 只针对连续相同的失败结果。第一次触发后，Runtime 给模型反馈并清空窗口，让新方案有机会运行；`max_recovery` 限制这样的机会。它是固定规则，不是另一个规划模型。

读 [Runtime.step](../../src/aster/runtime.py)，依次找到 `stall.observe`、`_recovery_used` 和 `checkpoint`。再运行：

```bash
conda activate aster
python -m pytest -q tests/test_runtime_repairs.py -k stall_progress
```

这个检查先连续修改不同文件，再重复读取同一文件，最后恢复 checkpoint 后继续重复。你应看到：真实修改不中断；重复行为触发恢复；重启不会把已花掉的恢复次数变回零。

边界：相同操作偶尔可能有业务意义，规则无法理解全部任务意图。因此停止原因会写入 trace，而不是把所有停止都记作“任务失败于模型能力”。

## 问题：裁掉旧消息，为什么会收到接口格式错误？

模型一轮可以同时要求读 A、读 B。下一次请求必须保留“这两个请求”和“这两个结果”的对应关系。如果只取列表最后 40 条，可能剩下 B 的结果，却丢了 B 的请求。`call_id` 就像取件编号：结果必须能找到对应的调用。

[history_groups](../../src/aster/provider.py) 把推理元数据、一次调用批次及对应结果组成不可拆开的组。Runtime 保留供应商原生返回结构；Responses 的 reasoning 块、Chat 的推理字段，以及 Anthropic thinking 块所需的签名不会被重写成普通文字。模型一轮中途停止时，缺少返回的调用记录 `cancelled` 结果，再保存完整 checkpoint；若工具可能已产生副作用，会提示检查工作区，不能把取消当作确定未执行。这里保留的是协议需要的数据，不是在 trace 中宣称额外读取了模型隐藏推理。

读代码的顺序是：`Runtime.turn` → `history_groups` → `ContextBuilder.build` → [request_payload](../../src/aster/provider.py)。最后一个函数同时供预算计数和真正发送请求使用，因此工具 schema 也计入输入。裁剪优先移除可丢弃的旧观察/旧交互，再让可重建的记忆和技能文本让出空间；当前任务和必须配对的最近交互无法容纳时明确停止。

```bash
python -m pytest -q tests/test_context_provider.py
python -m pytest -q tests/test_review_regressions.py -k 'reasoning or observation'
```

重点观察四种情况：一轮多个工具、超过旧切片长度的历史、包含推理元数据的返回，以及当前请求本身已经超过预算。最后一种情况的正确结果是拒绝请求，并保留原始任务；不是把用户请求截断后假装满足预算。

边界：本地估算满足预算，不等于供应商使用完全相同的 tokenizer 或图片计费规则。Chat 与 Anthropic 的测试通过只能证明本地适配合同，真实网关评测目前使用 Responses。切换协议也不能转换另一供应商的不透明推理元数据。

## 问题：为什么恢复任务需要两种“快照”？

哈希快照回答“文件有没有变”，文本基线回答“究竟改了哪些行”。例如原文件是 `port = 8080`，SHA256 是一串摘要；把摘要当作旧文件内容去生成 diff，当然会得到错误补丁。

[Workspace.snapshot](../../src/aster/workspace.py) 返回路径到“权限位加文件内容”的哈希映射，用于漂移判断；`text_snapshot` 保存运行起点的可读文件内容，`mode_snapshot` 保存权限位，用于 diff。`chmod +x` 因此也算修改，mtime 不参与判断。[Runtime.checkpoint / resume](../../src/aster/runtime.py) 保存并恢复这些信息，还包括 `structured_history`、`recovery_used`、`stall_items` 与 `RunState`。恢复前同时检查工作区指纹和有效运行配置；两者任一变化，都拒绝自动接着执行。

```bash
python -m pytest -q tests/test_runtime_repairs.py -k 'diff_resume or recovery_persistence'
```

打开测试中的 `checkpoint.json`，对比 `workspace` 与 `initial_text` 的值。恢复后的 `git_diff` 应显示 `-old`、`+new`，而不是一行哈希。旧版本缺少文本基线的活动 checkpoint 会被明确拒绝，不能从哈希反推出原文件。

边界：恢复的是 Harness 状态，不是操作系统内存或正在运行的进程。文本 diff 不覆盖所有二进制文件；Hash 仍可检测二进制变化。已经结束的任务不会因 `resume` 自动重新开始。

## 问题：Hooks 到底插在哪里，完成时会不会漏掉？

[Hooks](../../src/aster/hooks.py) 只有四个入口：`before_tool` 在工具副作用前检查；`after_tool` 处理成功；`after_failure` 处理失败；`stop` 处理运行结束。插件返回 `HookResult(action, feedback, metadata)`：`action` 决定继续、拒绝或停止；`feedback` 是供模型或人阅读的说明；`metadata` 是附带诊断信息。它不另建一套执行引擎。

Runtime 在完成、失败、取消时统一调用幂等的 `_stop_hook`；幂等是指重复调用收尾也只执行一次。前置 Hook 出错时拒绝动作，不能因为检查代码报错反而放行动作；成功工具的修改仍要被观察和记录，然后才遵从后置 Hook 的停止决定。

```bash
python -m pytest -q tests/test_runtime_repairs.py -k 'hook or cancellation'
python -m pytest -q tests/test_review_regressions.py -k after_tool_stop
```

把测试里的 `StopCounter.phases` 看作最小运行仪表：成功应记录一次 `completed`，取消应记录一次 `stopped`。Hooks 的独立收益由固定场景消融检查，见 [评测说明](../benchmark.md)；测试“Hook 被调用”本身不能证明任务质量变高。

## 问题：用户还没点同意，后台能否先运行？

不能。[TUI](../../src/aster/tui.py) 把运行放进 Textual 的后台任务，通过原生模态框等待审批或问题答案。审批默认焦点是 **Deny**；**Allow once** 只批准当前动作；Escape 取消当前询问。输入框不会因为等待模型而冻结。Ctrl+C 取消任务，运行层等待子进程和 Worker 清理；Ctrl+Q 退出界面。

[WorkerManager](../../src/aster/workers.py) 给运行中的子任务维护一个短消息队列。`worker_message` 返回 `queued` 只代表排队成功，消息在下一轮开始前才交给子任务；已完成的 Worker 返回 `worker_finished`，需要新建子任务。子任务继承父任务的只读、计划模式、审批规则、Hooks 和网络范围，写入范围取交集。它有自己的会话工作记忆，但不写共享的跨运行记忆；已完成的任务释放并发名额。

```bash
aster --tui --cwd examples/demo
# 无模型密钥也可以运行真实界面交互测试：
python -m pytest -q tests/test_interaction_workers.py
```

测试会真正打开无显示窗口的 Textual 应用，按下拒绝、提交回答、取消等待，并检查结果是否进入下一轮。Worker 的消息方式借鉴了 [Pi 的 steering queue](https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/agent.ts)：先排队，到稳定边界消费，不直接修改正在处理中的工具调用。

边界：Worker 共享项目工作区，不是完整独立 Git worktree；写入范围冲突会被拒绝，沙箱回写也检查目标文件是否被别人改过。`worker_status` 可查看进展，父任务终态还会自动汇总当前 Worker 修改；新任务使用新的 WorkerManager，旧编号不能给新任务贡献修改记录。Hooks 插件由父子共享，需要按 `run_id` 区分自身的运行状态。

## 问题：网关断开一次，是否必须重跑整个任务？

[ModelClient._post](../../src/aster/provider.py) 只对连接/超时和部分瞬时 HTTP 错误做有上限重试，最多三次。无效参数一类的拒绝不重试。`ProviderError.details` 分开记录 `category`（类别）、`status`（HTTP 状态）、`code`（供应商代码）、`message`（脱敏说明）、`attempts`（本次请求已尝试次数）。

重试范围只覆盖模型生成请求；工具执行发生在完整模型回复之后，写文件等副作用不会被这一层重复执行。代价是供应商可能已处理但响应丢失的请求仍产生费用，重试并不保证只计费一次。测试 `tests/test_context_provider.py` 中的 `retry`、`invalid_request` 和 `transport_error` 可以分别验证这些情况。

Responses 的 `incomplete`、Chat 的 `length`、Anthropic 的 `max_tokens` 表示回复尚未完整结束。它们会成为 `incomplete_response`，不能把半句答案当 Final；已返回的 usage 仍累计到该次尝试，不因失败而漏记 token。`tests/test_provider_events.py` 同时检查这件事，以及 UI 监听器报错不能影响真实工具结果和 checkpoint。

## 再看一个容易漏掉的细节：如何发现尚未加载的工具

核心集合只有 9 个工具，但模型需要知道其余能力叫什么。在 [tools.py](../../src/aster/tools.py) 的 `definitions()` 中，`tool_load` 参数 `names` 的 `enum` 列出全部可用名称，相当于简短目录；具体工具的说明与参数结构只在加载后进入下一轮。这里的 `enum` 就是允许选择的值列表，不是另一套工具注册系统。

Runtime 先检查整个名称列表，混入未知名称就整批拒绝，避免模型以为加载成功。`test_review_regressions.py` 会让一个模拟模型只看到核心定义，从目录找到 `todo_add`，加载后在下一轮真正执行，验证发现、加载和调用三个环节都连通。目录文字也计入输入预算，因此成本评测没有漏算这部分。
