# 控制任务轨迹审计（阶段记录）

更新于 2026-09-30。本文件是已完成 episode 的只读证据审计，不是整轮评测结论，也不改变冻结评分或源码。阶段一审计 transpose 第一轮三臂，阶段二补齐 transpose 与 grade-school，阶段三补齐 BigCodeBench，阶段四补齐 Forth。目前覆盖三道简单控制题的 18 条与中等难度耦合控制题 Forth 的 6 条，合计 **24 个 episode，每 arm 八条**。其他任务不在当前审计范围。审计没有重新执行模型或测试。

`solo` 与 `worker` 均绑定新源码 `a3d3364147fdb27eeaf9d1308317fbf594137611`，`current` 绑定旧源码 `6ccb8be1ef994f490eaa79a3786c0fb9c18df7e4`。只有同源码的 solo/worker 对比隔离了 Worker 可用性；current/worker 是整个系统的前后比较。下列 token 均为供应商已报告用量，cached input、reasoning 分别包含在 input、output 中，不能再次相加。无 usage 的 HTTP 尝试不视为免费；没有确认单价，不换算美元。

## 阶段一：transpose / repeat-01

三个 arm 均通过冻结独立评分 12/12，均 `completed / verified`。新 worker arm 没有启动子任务，不能将其表现记为子 Agent 实际贡献。

| arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| solo | 115,327 | 2,511 | 74,752 | 1,114 | 117,838 |
| current | 135,929 | 4,215 | 90,624 | 2,340 | 140,144 |
| worker | 81,767 | 2,737 | 44,032 | 1,865 | 84,504 |

| arm | 模型调用 | HTTP 尝试 / 无 usage | 工具调用 | 协调调用 | 启动 / 完成子任务 | 秒（不含评分） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| solo | 11 | 11 / 0 | 18 | 0 | 0 / 0 | 77.444 |
| current | 14 | 15 / 1 | 19 | 2 | 1 / 1 | 139.005 |
| worker | 8 | 8 / 0 | 10 | 0 | 0 / 0 | 74.674 |

协调调用按冻结统计口径计 `worker_*` 工具，已经包含在工具调用总数内。独立评分分别耗时 0.061、0.060、0.061 秒。current 的额外尝试分类为 `transport`，无 usage，耗时 0.136 秒；不能将一次墙钟差异全部归因于委派或重试。

### current 的交付证据链

- **分工**：父运行 `c14fda8a6691431ea75cf93b2ee026ea` 的 `worker_started`（seq 156）把接口、stub 状态、ragged rows、空格、空输入与尾换行的调查交给只读 Explore `7576566296b841588d07a6f5156a1220`。父任务声明自己检查 metadata 并在实现后测试。
- **实际动作**：子运行 list/search 后读取 `transpose.py`、`transpose_test.py`、`.docs/instructions.md`，共 6 次工具、4 次模型调用；对子模块 stub 执行一次 unittest，12 项全部失败，未修改文件。父在委派至交付期间没有独立工具工作，仅发送说明并通过 `worker_status` 等待；该等待调用记录 37.802 秒，子运行起止约 51.359 秒。
- **交付与消费**：子 `handoff.json` 是完整 findings，明确未改文件、stub 测试失败。父 seq 241 收到 status 结果；交付正文作为 `function_call_output`（`call_A89SpayRi8CwpKSp4keRfPln`）进入父请求 `0005` 至 `0010`。因此可确认送达，不能仅凭送达认定收益。
- **后续动作**：父 seq 253、256 重读实现文件和完整 83 行测试，seq 495 宣布按每列最后有效行实现，seq 499 自行 patch，seq 522 的正式测试通过。父随后修正一个自己编写的错误额外断言，再完成交付。
- **重复读取的限度**：子读的 3 个文件有 2 个被父重读；父编辑前读取源码可提供必要的修改依据，不能一概记为无效。完整测试文件被双方调查、子完成后父才开始实现，则没有展示省去父调查或实施步骤的效果。原请求本身已经包含左右补空格及保留实际空格的要求，子总结不是独立新增需求。
- **报告正确性边界**：子关于 `text.split("\n")` 会让尾空行把 `"A\n"` 变为 `"A "` 的判断没有执行证据，并与“Don't pad to the right”及它自身“最后有效行之后不补空格”的说明冲突。父最终实现使用最后有效行边界；不能将子 `completed / verified` 当作每项解释都正确。

| current 角色 | Input | Output | Cached input | Reasoning | Total | 模型 | 工具 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 父 | 101,736 | 2,006 | 72,704 | 869 | 103,742 | 10 | 13 |
| 子 | 34,193 | 2,209 | 17,920 | 1,471 | 36,402 | 4 | 6 |

本次结论是“存在有来源、已送达的调查交付，但未证明增量任务收益或减少工作”。新 worker 选择不派工，可记录为该轮避免启动委派；不能写成子 Agent 带来了 12/12 或 token 节约。solo 另有 7 次 todo 操作和一次加载这些工具，worker 没有这些操作，说明调用差异还包含工作流程选择。

证据位于 `artifacts/private/worker-owned-20260930/repeat-01/` 下的 `runs/aider-python-transpose--{solo,current,worker}/{result.json,request-ledger.json,grading/grade.json}`，以及 `workspaces/aider-python-transpose--current/.aster/runs/` 下上述父子编号的 `trace.jsonl`、`handoff.json` 与 `requests/`。原始私有轨迹不复制进本报告。

## 阶段二：其余九条已完成记录

transpose / repeat-02 三臂均独立通过 12/12；grade-school 两次三臂均独立通过 20/20。这九条均 `completed / verified`，没有 episode error。它们与阶段一合计为四个完整三臂组、每 arm 四次独立 pass@1；不是 best-of-two，也不把子测试数池化成任务通过率。

### 用量与过程账本

| task / repeat / arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| transpose / 02 / solo | 149,470 | 3,473 | 119,296 | 1,660 | 152,943 |
| transpose / 02 / current | 139,800 | 2,822 | 86,016 | 1,139 | 142,622 |
| transpose / 02 / worker | 176,218 | 3,804 | 123,904 | 1,922 | 180,022 |
| grade-school / 01 / solo | 60,769 | 1,215 | 43,520 | 353 | 61,984 |
| grade-school / 01 / current | 56,924 | 1,055 | 28,160 | 214 | 57,979 |
| grade-school / 01 / worker | 68,780 | 1,138 | 38,400 | 299 | 69,918 |
| grade-school / 02 / solo | 53,312 | 1,073 | 44,032 | 324 | 54,385 |
| grade-school / 02 / current | 57,828 | 1,296 | 47,104 | 310 | 59,124 |
| grade-school / 02 / worker | 57,715 | 1,043 | 44,032 | 210 | 58,758 |

| task / repeat / arm | 模型调用 | HTTP 尝试 / 无 usage | 工具调用 | 协调调用 | 启动 / 完成子任务 | 秒（不含评分） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| transpose / 02 / solo | 13 | 13 / 0 | 21 | 0 | 0 / 0 | 94.674 |
| transpose / 02 / current | 14 | 14 / 0 | 19 | 2 | 1 / 1 | 64.086 |
| transpose / 02 / worker | 14 | 15 / 1 | 20 | 0 | 0 / 0 | 126.011 |
| grade-school / 01 / solo | 6 | 6 / 0 | 9 | 0 | 0 / 0 | 91.695 |
| grade-school / 01 / current | 6 | 6 / 0 | 9 | 0 | 0 / 0 | 103.705 |
| grade-school / 01 / worker | 7 | 7 / 0 | 9 | 0 | 0 / 0 | 39.163 |
| grade-school / 02 / solo | 6 | 6 / 0 | 7 | 0 | 0 / 0 | 54.466 |
| grade-school / 02 / current | 6 | 6 / 0 | 11 | 0 | 0 / 0 | 113.781 |
| grade-school / 02 / worker | 6 | 6 / 0 | 9 | 0 | 0 / 0 | 35.893 |

新增的无 usage 尝试仅出现在 transpose / 02 / worker，分类也是 `transport`。12 条记录均已核对：父子运行用量之和、request-ledger 请求用量之和、HTTP attempts 的已报告用量之和与 result 用量一致；模型、HTTP、工具计数一致，`source_unchanged=true`。这是对已保存记录的静态对账，没有重跑评分。

### transpose / repeat-02：并行调查重叠，后续实现仍由父完成

旧 current 父运行是 `e60d5a61e9f542daa5a1e5ae090ac9a1`，子运行是 `d475a9c9c791471fb2340adeefd17fd4`。

- 父 seq 130/132 派出 Explore，任务是找接口、精确路径及测试边界，声明自己也会查仓库并实现。父 seq 143/146 list/search，158/161/164 读取实现、测试和 instructions；子 seq 12/15 search/list，27/30/33 读取同三个文件。双方对这三个文件的读取都在交付前，且父先于子读到它们。父源码读取可以服务之后的安全修改，但本次委派没有替代父的源码或测试调查。
- 子第六次工具调用（seq 44）跑 stub 测试，12 项失败，未改文件。子交付整理接口和已读规范；父接收前的 seq 243 已明确说出“按每列最后有字符的行截断、保留真实空格”的实施原则。父 seq 246 的 status 在子已完成后立即返回；没有 repeat-01 那样的长 status 等待。
- 交付进入父请求 `0006` 至 `0010`，对应 `function_call_output` `call_ZxVI4XboFBOTECYQDOWHMztU`。父 seq 434 自行 patch，494 跑正式测试，497 重读已改文件，600 跑额外空格/空行/尾换行断言。可以确认接收及后续实施，但没有证据显示子交付改变了此前已经明确的方案、提供了修复或省去了某个父步骤。

| current / repeat-02 角色 | Input | Output | Cached input | Reasoning | Total | 模型 | 工具 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 父 | 105,958 | 1,931 | 67,072 | 956 | 107,889 | 10 | 13 |
| 子 | 33,842 | 891 | 18,944 | 183 | 34,733 | 4 | 6 |

新 worker 仍未派工。其父运行 `de7fdd7355b6458c97eee6c1293129c2` 首次把输入行解释为右对齐（公开回复 seq 159/427），第一次正式收集到的测试 seq 539 失败，之后 seq 635 解释修正为输入左对齐、输出按缺失位置补空格，seq 923 再 patch、948 正式测试通过。额外断言的错误预期还在 seq 1105/1177 之间被修正。它的 180,022 tokens 高于同次 solo 的 152,943；因此不能把 repeat-01 的较低 token 当作新机制稳定节约的证明。

### grade-school 两次：六条均没有实际委派

两次三臂均由父完成仓库检查、读取 stub 和测试、修改 `grade_school.py`、运行原测试。没有 worker_start、子运行或 handoff，也没有验收 todo 带来的协调过程。所有 arm 都曾用默认 `python -m unittest -v` 得到 `no_tests_collected`，随后显式指定 `grade_school_test` 并通过。current / repeat-02 还有一个 `run_shell` 因 `approval_unavailable` 未执行；其后正式测试仍通过，不能把该额外断言记为已运行。

这一组说明了速度解释的限制：repeat-01 current 与 worker 都是 9 次工具，模型调用分别 6、7 次，但墙钟分别 103.705、39.163 秒。请求账本记录的模型请求耗时和分别为 102.694、38.039 秒；repeat-02 current/worker 的模型调用均为 6 次，请求耗时和仍分别为 112.656、34.882 秒。没有子任务并行参与这些运行，不能把墙钟差异写成委派并行收益，也不能从少量请求判定稳定服务延迟差异。

### 已审计 12 条的描述性合计

| arm | 任务通过 / 正常正确交付 | Total tokens | 模型调用 | 工具调用 | 协调调用 | 实际启动子任务 | 墙钟秒合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| solo | 4/4 · 4/4 | 387,150 | 36 | 55 | 0 | 0 | 318.279 |
| current | 4/4 · 4/4 | 399,869 | 40 | 58 | 4 | 2 | 420.577 |
| worker | 4/4 · 4/4 | 393,202 | 35 | 48 | 0 | 0 | 275.741 |

新 worker 在四次运行中都没有启动子任务，旧 current 在两次 transpose 上都派出 Explore，但两次都没有观察到可以单独确认的增量任务贡献。新 worker 相对 solo 的工具调用合计较少，已报告 token 却略多。当前只能说这些小任务上保留了冻结通过率、且新 worker 没有强制委派；不能称为实际委派获得了通过率、token 或时间收益。

阶段二证据按相同目录规则位于 `repeat-01` / `repeat-02` 下：`runs/aider-python-{transpose,grade-school}--{solo,current,worker}/` 的 result、ledger 与 grade，以及相应 workspace 的 `.aster/runs/`。只有上述 current 两条存在子运行；其余 episode 的用量全部属于父运行。

## 阶段三：BigCodeBench-25 两次三臂

六条均独立通过冻结原测试 5/5，均 `completed / verified`，没有 episode error、子运行或协调调用。六份最终 `solution.py` 均通过一次 patch 实现同一处理链：`json.dumps` 编码 UTF-8，再 zlib 压缩，最后 Base64 编码并返回 ASCII 字符串。代码生成、检查和测试都由父 Agent 完成，不存在可归因于委派的产物。

| repeat / arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| 01 / solo | 86,245 | 2,885 | 50,688 | 1,388 | 89,130 |
| 01 / current | 117,670 | 3,191 | 93,184 | 1,700 | 120,861 |
| 01 / worker | 129,754 | 2,743 | 107,008 | 1,165 | 132,497 |
| 02 / solo | 63,555 | 1,851 | 55,296 | 790 | 65,406 |
| 02 / current | 73,296 | 1,921 | 60,928 | 774 | 75,217 |
| 02 / worker | 90,374 | 2,892 | 77,824 | 1,447 | 93,266 |

| repeat / arm | 模型调用 | HTTP 尝试 / 无 usage | 工具调用 | 协调调用 | 启动 / 完成子任务 | 秒（不含评分） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 01 / solo | 10 | 10 / 0 | 15 | 0 | 0 / 0 | 95.268 |
| 01 / current | 13 | 13 / 0 | 14 | 0 | 0 / 0 | 98.474 |
| 01 / worker | 14 | 14 / 0 | 16 | 0 | 0 / 0 | 85.175 |
| 02 / solo | 8 | 8 / 0 | 10 | 0 | 0 / 0 | 74.889 |
| 02 / current | 9 | 11 / 2 | 9 | 0 | 0 / 0 | 56.761 |
| 02 / worker | 10 | 10 / 0 | 12 | 0 | 0 / 0 | 80.898 |

current / repeat-02 的两次额外尝试均为 `transport` 且没有 usage，分别记录 0.144、0.139 秒。其余五条没有额外无 usage 尝试。所有用量均属于父运行。

### 本地验证路径的差异

六条都先尝试 unittest discovery 并得到 `no_tests_collected`，之后各自在根目录生成 smoke tests，正式验证通过。第一轮三臂各收集到一个自写测试，第二轮三臂各两个。这些测试只解释 Runtime 为何允许完成；表中的独立 5/5 来自不可变原评分测试，不能用自写测试替代。

四条在到达根目录测试前还走过 `.tmp` 路径，增加了验证步骤：

- repeat-01 solo，父 `cf6f22ef4da7428ca3cfdfc7bbaa0261`：seq 590 写 `.tmp/test_solution.py`，625 未收集到测试，882 改写根目录 `solution_test.py`，915 正式验证通过。
- repeat-01 current，父 `186bce37200d4e3b9929a9e08c4c7d3f`：seq 873 写 `.tmp/test_solution_local.py`，900 随后的 read_file 报 `FileNotFoundError`；1149 写根目录测试，1173 验证通过。
- repeat-01 worker，父 `c0287cda517049de8bd0320a2b18f321`：seq 700 写 `.tmp/test_solution.py`，735 未收集测试，807 的 pytest 报文件不存在；1052 改写根目录测试，1077 验证通过。之后 seq 1146 的删除命令被 `approval_unavailable` 拒绝，不能记为已经清理。
- repeat-02 worker，父 `97c56573820d436ab010fe3a3417bc05`：seq 718 写 `.tmp/test_solution.py`，753 未收集测试，1016 改写根目录测试，1040 验证通过。

这里仅记录实际工具返回，不从轨迹反推 `.tmp` 不可见的实现根因，也不在付费评测进行中修改冻结代码。repeat-02 solo 与 current 直接生成根目录测试；其中 current 的另一个 shell 检查因 `approval_unavailable` 未执行。读取修改前后源码、检查 diff 可以有合法验证用途，不能把所有附加工具一概算成浪费。

BigCodeBench 两次中，新 worker 都没有启动孩子，但已报告 token 均高于同次 solo，模型调用也分别为 14 对 10、10 对 8。轨迹只支持“结果正确，实际验证路径与调用数量不同”，不支持“新委派降低成本”。第一轮新 worker 墙钟较短、第二轮较长，也不能从这两次把时间差归因于不存在的子任务并行。

证据位于两个 repeat 的 `runs/bigcodebench-25--{solo,current,worker}/` 与对应 workspaces。六条 result 与独立 `grading/grade.json` 一致；父运行、请求和 HTTP attempts 的已报告用量均对账一致。

## 三道简单题的完整描述性合计（18 条）

下列数字按三题各两次独立 pass@1、每 arm 六条汇总，与 `summary.json` 的 `difficulty.simple` 逐字段核对一致。全体六组三臂均通过，因此这里的所有样本、共同通过样本及共同正常正确交付样本相同；没有挑选较好的一次。

| arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| solo | 528,678 | 13,008 | 387,584 | 5,629 | 541,686 |
| current | 581,447 | 14,500 | 406,016 | 6,477 | 595,947 |
| worker | 604,608 | 14,357 | 435,200 | 6,908 | 618,965 |

| arm | 任务通过 / 正常正确交付 | 模型调用 | 工具调用 | 协调调用 | 启动 / 完成子任务 | HTTP 尝试 / 无 usage | 墙钟秒合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| solo | 6/6 · 6/6 | 54 | 80 | 0 | 0 / 0 | 54 / 0 | 488.436 |
| current | 6/6 · 6/6 | 62 | 81 | 4 | 2 / 2 | 65 / 3 | 575.812 |
| worker | 6/6 · 6/6 | 59 | 76 | 0 | 0 / 0 | 60 / 1 | 441.814 |

current 的两个孩子均为前文 transpose Explore，合计 71,135 tokens、8 次模型、12 次工具；其父运行合计 524,812 tokens。solo、worker 的用量全部来自父。无 usage 尝试分别为 0、3、1 次，全部已知分类为 transport，其额外账单仍未知。

新 worker **6/6 没有启动孩子**，只证明这组小任务没有被强制委派、且最终通过率保持 6/6。它的已报告 total token 为 **618,965，高于 solo 的 541,686，增加 14.3%**，也高于旧 current 的 595,947。虽然工具合计 76 次低于 solo 的 80 次，模型调用却为 59 次对 54 次。不能宣称降低 token、美元成本，或把较低墙钟合计算成实际委派收益。小样本中的提示、工作流程和服务请求耗时差异仍不能拆成各自的因果贡献。

## 阶段四：Forth 耦合控制题两次三臂

`aider-python-forth` 的冻结分类是 `medium / coupled_control`，不能将它并入前面三道简单题的 18 条统计。六条均独立通过 54/54，均 `completed / verified`，没有 episode error 或无 usage HTTP 尝试。新 worker 两次均没有启动孩子；旧 current 只在第二次启动一个 Explore。

| repeat / arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| 01 / solo | 199,141 | 4,616 | 117,760 | 1,791 | 203,757 |
| 01 / current | 174,881 | 3,609 | 121,856 | 1,124 | 178,490 |
| 01 / worker | 184,700 | 4,286 | 132,608 | 1,727 | 188,986 |
| 02 / solo | 162,642 | 4,074 | 130,560 | 1,324 | 166,716 |
| 02 / current | 307,809 | 6,599 | 226,816 | 1,726 | 314,408 |
| 02 / worker | 154,454 | 3,149 | 113,664 | 948 | 157,603 |

| repeat / arm | 模型调用 | HTTP 尝试 / 无 usage | 工具调用 | 协调调用 | 启动 / 完成子任务 | 秒（不含评分） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 01 / solo | 13 | 13 / 0 | 21 | 0 | 0 / 0 | 112.285 |
| 01 / current | 12 | 12 / 0 | 13 | 0 | 0 / 0 | 212.239 |
| 01 / worker | 12 | 12 / 0 | 18 | 0 | 0 / 0 | 114.686 |
| 02 / solo | 12 | 12 / 0 | 25 | 0 | 0 / 0 | 133.095 |
| 02 / current | 21 | 21 / 0 | 29 | 2 | 1 / 1 | 183.612 |
| 02 / worker | 12 | 12 / 0 | 17 | 0 | 0 / 0 | 90.715 |

### current / repeat-02 的实际分工与父方使用

父运行 `43e93c2bc13749148c9712159a602387` 派 Explore `e15489a5b2a94de69cd264d28134e1f4` 调查 API、异常、算术、定义绑定和测试边界，自己负责文档、实现与最终验证。父 seq 188/191 确实先读两份 instructions，然后 seq 254 的 status 等待 39.925 秒，存在文档检查与子调查重叠的工作。

子实际 list/search、读取 `forth.py`、完整 `forth_test.py` 和补充 instructions；一个用于显示行号的 shell 命令被拒绝后，又做两次 search。合计 9 次工具、4 次模型，未编辑文件、未执行测试，最终 verification 为 missing。交付指出定义时绑定、内置词覆盖和精确异常等约束，并明确哪些边界未被现有测试覆盖。

交付经 worker_status 进入父请求 `0006` 至 `0017`。父接收后 seq 265/268 重读实现与测试；测试文件共 263 行，1–320 的请求覆盖全文件。父在 seq 786 说明采用编译后固定词体以保存定义快照，790 自行实现。这个方案与子说明一致，但父也重新读取了相同测试，不能只据一致性认定子报告改变了方案或省掉调查。源码读取可以是必要编辑依据；子读的三个文件都被父读取过，并不自动等于三次无效读取。

父随后调整异常兼容时触发一次 `fresh_read_required`（seq 1073），补读后修改。正式测试 seq 1316 暴露用户词覆盖内置词时的分发错误，父 seq 1390 自行诊断、1915 修复，1938 的 54 项测试通过，最终另有成功的接口 smoke check。子交付此前已经描述了覆盖规则，但既没有实现修复，也没有在实现后找出这个错误。此处可确认有用规范被整理并送达、父在交付后实现；尚无独立证据证明这次调查带来额外正确性或节省工作。旧 current 第一轮没有子任务也经历实现修正，不能把第二轮的修正次数单独归因于委派。

| current / repeat-02 角色 | Input | Output | Cached input | Reasoning | Total | 模型 | 工具 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 父 | 265,319 | 4,828 | 212,480 | 1,297 | 270,147 | 17 | 20 |
| 子 | 42,490 | 1,771 | 14,336 | 429 | 44,261 | 4 | 9 |

### common acceptance todo：有可见步骤，也有语义边界

新 solo 与新 worker 都使用相同的验收待办规则；旧 current 这两条没有 todo。Forth 新源码四条中的八个验收 todo 最终全部 `done` 且有 `resolution`，没有未关闭或取消项，也没有 `completion_revision` 或 `requirement_wait`。这些任务没有证明机制在真实模型运行中拦住了提前 Final，不能用最终全闭合倒推曾发生有效拦截。

| repeat / arm | 最终验收事项数 | todo 工具调用 | 其中被拒绝 | 加载 todo 工具 | 最终未闭合项 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 01 / solo | 2 | 5 | 0 | 1 | 0 |
| 01 / worker | 1 | 2 | 0 | 1 | 0 |
| 02 / solo | 3 | 10 | 1 | 1 | 0 |
| 02 / worker | 2 | 4 | 0 | 1 | 0 |

todo 调用含 add/update/list，已经计入工具总数。solo / repeat-02 的一次更新随带错误参数的 read_file 整批被拒绝，之后重新执行。这些是真实发生的流程操作与用量，但没有禁用 todo 的同条件消融，不能把所有 token 或耗时差异直接算成它的净成本；请求还包含实现、已有上下文与测试结果。

状态与真实验证仍需分开：worker / repeat-02 的实施事项在 seq 800 标 done，首次 run_tests 是 seq 855，实际收集并通过 54 项测试是 seq 955；不能把 seq 800 当成已测试的证明。新源码四条的额外边界 shell 都曾因 `approval_unavailable` 未执行，最终 resolution 主要引用原 54 项测试。这保证了冻结评分覆盖的最终行为，但不证明每一个自行提出的边界都被独立检查。`requirement_reviewed` 事件也会在 in_progress 更新时产生，不能直接把事件数量当成语义验收次数。

Forth 两次新 worker 的 token 和工具调用均低于同次 solo，但两次都没有实际子任务，且 solo 的 todo、错误调用及修正路径不同。可以记录两次结果和过程差异，不能称为实际委派的收益。

Forth 证据按相同目录规则位于 `repeat-{01,02}/runs/aider-python-forth--{solo,current,worker}/` 与对应 workspace 的 `.aster/runs/`。六份独立 grade 与 result 一致，所有父子、请求、HTTP 用量及调用数已对账。

## 全部已审计控制任务（24 条）

本表是三道简单题加一道 medium 耦合题的描述性合计，不取代前面的难度分层。四题各两次独立 pass@1，每 arm 八条，三臂都 8/8 通过并正常交付。

| arm | Input | Output | Cached input | Reasoning | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| solo | 890,461 | 21,698 | 635,904 | 8,744 | 912,159 |
| current | 1,064,137 | 24,708 | 754,688 | 9,327 | 1,088,845 |
| worker | 943,762 | 21,792 | 681,472 | 9,583 | 965,554 |

| arm | 任务通过 / 正常正确交付 | 模型调用 | 工具调用 | 协调调用 | 启动 / 完成子任务 | HTTP 尝试 / 无 usage | 墙钟秒合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| solo | 8/8 · 8/8 | 79 | 126 | 0 | 0 / 0 | 79 / 0 | 733.816 |
| current | 8/8 · 8/8 | 95 | 123 | 6 | 3 / 3 | 98 / 3 | 971.664 |
| worker | 8/8 · 8/8 | 83 | 111 | 0 | 0 / 0 | 84 / 1 | 647.215 |

旧 current 三个子任务共消耗 115,396 tokens、12 次模型、21 次工具，父方共 973,449 tokens。新 worker 八条全部未派工，total tokens 965,554 仍高于新 solo 的 912,159；工具较少与墙钟合计较短不构成实际委派节省成本的证据。本组目前支持“在这些控制题上没有强制派工、冻结通过率没有下降”，尚未证明子 Agent 在可分解任务上的价值。

## 尚未审计范围

Django、SymPy、gRPC、COBOL、OpenLibrary 等剩余任务尚未在本文件审计。当前控制题均为已见开发回归，不能外推全部复杂任务或整体 Worker 价值。后续仍需按分工、实际动作、交付送达、父方使用与结果变化逐条补证据。
