# Aster / pi / Codex：50 题同模型比较

已完成全部 150 次尝试和独立评分。

固定 50 题：SWE-bench Verified 20、Terminal-Bench 8、Aider Polyglot 12、BigCodeBench 10。旧 20 题已参与开发，新增 30 题单列；三者本轮全部重新运行，未拼接历史最好成绩。

| Agent | 独立通过 / 50 | 正常交付且通过 | 请求数 | 已报告总 token | 缺用量请求 | 累计答题分钟 |
|---|---:|---:|---:|---:|---:|---:|
| aster | 36/50 (72%) | 32/50 | 842 | 16,790,987 | 24 | 351.4 |
| pi | 37/50 (74%) | 36/50 | 608 | 12,666,648 | 5 | 166.7 |
| codex | 36/50 (72%) | 36/50 | 538 | 12,198,586 | 7 | 171.3 |

## 按来源

| 分组 | 题数 | Aster | pi | Codex |
|---|---:|---:|---:|---:|
| Aider Polyglot | 12 | 11/12 | 11/12 | 12/12 |
| BigCodeBench v0.1.4 | 10 | 5/10 | 6/10 | 6/10 |
| SWE-bench Verified | 20 | 14/20 | 14/20 | 13/20 |
| Terminal-Bench archive | 8 | 6/8 | 6/8 | 5/8 |

## 旧题与新增题

| 分组 | 题数 | Aster | pi | Codex |
|---|---:|---:|---:|---:|
| new30 | 30 | 21/30 | 23/30 | 21/30 |
| old20 | 20 | 15/20 | 14/20 | 15/20 |

## 用量口径

| Agent | 非缓存输入 | 缓存输入 | 输出 | 超时题数 | 有请求异常的题数 |
|---|---:|---:|---:|---:|---:|
| aster | 8,608,802 | 7,928,832 | 253,353 | 8 | 20 |
| pi | 1,808,650 | 10,666,240 | 191,758 | 1 | 5 |
| codex | 1,534,552 | 10,484,224 | 179,810 | 1 | 6 |

缺失用量不当作零成本；没有可信供应商单价，不换算金额。请求异常包含客户端断连、读取错误和超时，不等于已证实的供应商故障，也不等于该题必然因此失败。

## 逐题结果

| 任务 | 来源 | 分组 | Aster | pi | Codex |
|---|---|---|---|---|---|
| `django__django-17029` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `django__django-11400` | SWE-bench Verified | old20 | 未通过（超时） | 未通过 | 未通过 |
| `sympy__sympy-23262` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `sympy__sympy-17630` | SWE-bench Verified | old20 | 未通过 | 未通过 | 未通过 |
| `sphinx-doc__sphinx-9230` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `sphinx-doc__sphinx-11510` | SWE-bench Verified | old20 | 未通过（超时） | 未通过 | 通过 |
| `pydata__xarray-7233` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `pydata__xarray-3677` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `pytest-dev__pytest-7571` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `pytest-dev__pytest-10356` | SWE-bench Verified | old20 | 未通过 | 未通过 | 未通过 |
| `psf__requests-5414` | SWE-bench Verified | old20 | 通过 | 通过 | 通过 |
| `psf__requests-2931` | SWE-bench Verified | old20 | 通过 | 未通过 | 通过 |
| `terminal-polyglot-c-py` | Terminal-Bench archive | old20 | 通过 | 通过 | 通过 |
| `terminal-cancel-async-tasks` | Terminal-Bench archive | old20 | 通过 | 未通过 | 通过 |
| `terminal-custom-memory-heap-crash` | Terminal-Bench archive | old20 | 通过（超时） | 通过 | 未通过 |
| `terminal-schemelike-metacircular-eval` | Terminal-Bench archive | old20 | 通过（超时） | 通过 | 通过 |
| `terminal-cobol-modernization` | Terminal-Bench archive | old20 | 通过 | 通过 | 通过 |
| `terminal-kv-store-grpc` | Terminal-Bench archive | old20 | 未通过 | 通过 | 未通过 |
| `aider-simple-linked-list` | Aider Polyglot | old20 | 通过 | 通过 | 通过 |
| `aider-sgf-parsing` | Aider Polyglot | old20 | 通过 | 通过 | 通过 |
| `astropy__astropy-14182` | SWE-bench Verified | new30 | 未通过（超时） | 通过 | 未通过 |
| `astropy__astropy-13579` | SWE-bench Verified | new30 | 通过 | 通过 | 通过 |
| `scikit-learn__scikit-learn-12973` | SWE-bench Verified | new30 | 通过 | 通过 | 未通过 |
| `scikit-learn__scikit-learn-25102` | SWE-bench Verified | new30 | 未通过（超时） | 未通过 | 未通过 |
| `matplotlib__matplotlib-26291` | SWE-bench Verified | new30 | 通过 | 通过 | 通过 |
| `matplotlib__matplotlib-26342` | SWE-bench Verified | new30 | 通过 | 通过 | 通过 |
| `pylint-dev__pylint-7277` | SWE-bench Verified | new30 | 通过（超时） | 通过 | 通过 |
| `pylint-dev__pylint-8898` | SWE-bench Verified | new30 | 通过 | 通过 | 未通过 |
| `terminal-headless-terminal` | Terminal-Bench archive | new30 | 通过 | 通过 | 通过 |
| `terminal-regex-chess` | Terminal-Bench archive | new30 | 未通过 | 未通过（超时） | 未通过（超时） |
| `aider-python-tree-building` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-python-zipper` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-javascript-robot-name` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-javascript-variable-length-quantity` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-javascript-go-counting` | Aider Polyglot | new30 | 通过 | 未通过 | 通过 |
| `aider-javascript-palindrome-products` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-cpp-meetup` | Aider Polyglot | new30 | 未通过（超时） | 通过 | 通过 |
| `aider-cpp-all-your-base` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-cpp-linked-list` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `aider-cpp-clock` | Aider Polyglot | new30 | 通过 | 通过 | 通过 |
| `bigcodebench-302` | BigCodeBench v0.1.4 | new30 | 通过 | 通过 | 未通过 |
| `bigcodebench-479` | BigCodeBench v0.1.4 | new30 | 未通过 | 通过 | 通过 |
| `bigcodebench-804` | BigCodeBench v0.1.4 | new30 | 未通过 | 未通过 | 未通过 |
| `bigcodebench-1052` | BigCodeBench v0.1.4 | new30 | 未通过 | 未通过 | 通过 |
| `bigcodebench-524` | BigCodeBench v0.1.4 | new30 | 通过 | 通过 | 通过 |
| `bigcodebench-35` | BigCodeBench v0.1.4 | new30 | 未通过 | 未通过 | 未通过 |
| `bigcodebench-19` | BigCodeBench v0.1.4 | new30 | 通过 | 通过 | 通过 |
| `bigcodebench-675` | BigCodeBench v0.1.4 | new30 | 通过 | 通过 | 通过 |
| `bigcodebench-931` | BigCodeBench v0.1.4 | new30 | 通过 | 通过 | 通过 |
| `bigcodebench-602` | BigCodeBench v0.1.4 | new30 | 未通过 | 未通过 | 未通过 |

## 复现与边界

Aster `c6b651d`、pi 1.1.0、Codex CLI 0.154.0；同模型 gpt-5.6-luna，medium，900 秒/题，64000 上下文，8192 输出上限，4 CPU / 8 GiB，6 槽并发。

这是公开任务的固定本地适配混合集，不是上游完整榜单。Python 仍占主导；公开数据可能被基础模型见过。单次 50 题仍不足以断言稳定排名；本轮没有产品改动前后对照，不能归因于验收或成本优化。

独立评分保留原测试断言。Aider/BigCodeBench 采用新容器，只转交指定解答文件；SWE 使用官方评分。Terminal 为保留本地服务在题目专属容器内注入测试，原断言的防作弊和覆盖能力有限。JavaScript 启用必做 xtest/xit，明确可选 test.skip 保留；C++ 启用全部必做测试。

[协议](PROTOCOL.md) · [冻结清单](manifest.json) · [正反预检](preflight.json) · [逐请求和逐题结果](results.json) · [完整性检查](audit.json) · [选题](../../datasets/general-coding-50-20261012/selection.json)

## 完成状态与异常核对

Aster 内部标记完成 40 题，其中 8 题未通过独立原始测试；另有 4 题产物通过，但未正常交付。

内部完成但测试失败：`sympy__sympy-17630`、`pytest-dev__pytest-10356`、`terminal-kv-store-grpc`、`bigcodebench-479`、`bigcodebench-804`、`bigcodebench-1052`、`bigcodebench-35`、`bigcodebench-602`。

通过但未正常交付：`terminal-custom-memory-heap-crash`、`terminal-schemelike-metacircular-eval`、`terminal-cobol-modernization`、`pylint-dev__pylint-7277`。

Aster 的 `astropy-14182` 提交主动删除随仓库提供的数据文件，导致 pytest 初始化失败；模型补丁本身成功应用。这是提交引入的问题，不记作外部环境故障。正则棋类题的 Aster 轨迹记录三次 120 秒流式请求超时，期间已收到响应事件，最终以 provider_error 结束；HTTP 200 后的 BrokenPipeError 不能证明供应商宕机。其余异常分类、完整性校验见 [最终审计](final-audit.json) 和 [失败证据](failure-notes.json)。

另有 Aster 的 C++ meetup 题超时后编译失败：实现中的两个方法名与头文件声明不一致，因此没有进入 90 项断言；参考解预检正常。这是候选代码问题。

末段也出现真实上游异常：pi 的 Requests 2931 收到 HTTP 503、围棋计数题收到 HTTP/2 流错误，两题均未通过；pi 的 gRPC 遇到流错误，产物仍通过但未正常交付。Codex 的 Requests 2931 和 SymPy 23262 也分别遇到 503 与流错误，原生恢复后仍通过。这些尝试均保留原结果，未追加重跑；一题之差不能据此解释为稳定能力排名。

## 上游数据质量限制

事后检查发现 BigCodeBench 35 的返回值文字与示例/测试矛盾；602 未在题干说明矩阵大小和制表符分隔；804 检查未说明的异常类型、精确 open/write 调用；1052 限定 pyplot.savefig，拒绝 figure.savefig。这些限制影响对失败原因的解释，尤其不能把 1052 的 mock 失败直接等同于未保存图片。本轮保留全部 50 题和原断言分数，没有据结果换题、重采样或抬高成绩。因此这是初版混合集，后续扩充前仍需审核题意与断言的一致性。详见 [数据质量记录](dataset-quality-notes.json)。

## 耗时与接入检查

| Agent | 答题中位秒 | 接入检查请求 | 接入检查已报告 token | 接入检查缺用量 |
|---|---:|---:|---:|---:|
| aster | 377.9 | 6 | 42,182 | 0 |
| pi | 160.9 | 4 | 23,568 | 0 |
| codex | 184.2 | 2 | 22,776 | 0 |

正式评测从首个任务启动到观察到控制进程退出约 143.0 分钟（上界，含容器准备与评分；不含前期环境预检和接入检查）。正式任务加接入检查合计 2,000 次请求、41,744,747 已报告 token、36 次缺失用量。

Aster 后台记忆请求已计入正式用量。本轮网关将所有请求统一为 medium 和 8192 输出上限，包括后台请求原本声明的 768 上限；该设置与原生默认配置不同，因此用量代表本轮固定协议。各客户端保留自身的请求超时和重试语义；整题预算相同不意味着单请求截止时间相同。

最终报告可用 `python evaluations/general-coding-50-20261012/finalize.py` 从保留的证据重新生成；该后处理脚本不修改冻结的执行协议、任务数据或原始得分。
