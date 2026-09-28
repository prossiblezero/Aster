# 前三道中等题：实际交付与贡献审计

状态：2026-09-28，Forth、REST API 与 Django-11532 共九次三臂运行已全部完成审计。只读已有 result、trace、request 和最终文件；未重新请求模型、运行测试或修改评测协议。表内为既有独立评分器结果，不是本次审计新跑的测试。

**当前观察：报告确实交付，但尚无独立贡献提升任务通过率的证据。** 三题实际启动 5 个只读 Explore，3 个完整交付、2 个步骤耗尽后部分交付，五份报告均进入父请求。Forth 主要确认父已实现的语义；REST API 的实现差异缺少可分离的因果证据。Django 旧臂提供了有用的共享入口建议，但父未落实；新版委派丢失原始失败场景，子报告被父引用来缩小修复范围，两个委派臂的最终代码均遗漏 SMTP，只有 solo 通过全部独立评分要求。

## 三臂结果

| 任务 | 臂 | 独立测试通过 | Worker 数 | 全队模型 / 工具调用 | 全队报告 token | 秒数 |
|---|---|---:|---:|---:|---:|---:|
| Forth | solo | 54/54 | 0 | 10 / 15 | 154,354 | 104.3 |
| Forth | handoff_only | 54/54 | 1 | 13 / 19 | 158,920 | 126.9 |
| Forth | redesigned | 54/54 | 0 | 16 / 22 | 282,462 | 429.5 |
| REST API | solo | 9/9 | 0 | 7 / 10 | 78,898 | 226.4 |
| REST API | handoff_only | 9/9 | 1 | 22 / 27 | 291,152 | 182.8 |
| REST API | redesigned | 9/9 | 1 | 16 / 20 | 177,234 | 150.1 |
| Django-11532 | solo | 要求项149/149 | 0 | 25 / 39 | 488,340 | 904.9 |
| Django-11532 | handoff_only | 要求项130/149，失败 | 1 | 37 / 61 | 760,059 | 606.2 |
| Django-11532 | redesigned | 要求项130/149，失败 | 1 | 18 / 46 | 285,157 | 758.8 |

Django 数值按独立评分器的 F2P/P2P 要求条目计数，不将旧版 unittest 解析中的 docstring 别名当成额外物理测试。模型列按结果文件请求计数；新版 Django 的 18 次包括最后一次无输出、无 token usage 的失败请求，285,157 是其余 17 次报告的 token，不能假定失败请求的实际消耗为零。单次时长受供应商延迟影响，token 不等于美元成本。新版 Forth 没有启动 Worker，其实现错误与额外消耗不能直接归因为子任务协作失败；新版 REST API 比 handoff_only 少消耗，也不能仅凭单次差异归因于共享预算或新生命周期。

## 证据定位方式

原始数据位于 `artifacts/private/worker-redesign-20260928/`。结果：`runs/<episode>/result.json`；轨迹与请求：`workspaces/<episode>/.aster/runs/<run_id>/{trace.jsonl,requests/}`。下文 P / C 表示父 / 子运行，`#` 是 trace 的 `seq`，不是跨运行通用序号。报告全文是否进入模型，按实际请求 JSON 的工具结果逐层解码核查，未仅依赖 status 或 delivered 标记。

## Forth：报告被引用，但关键实现已经完成

Episode：`aider-python-forth--handoff_only`。P=`6f5e678c53c844aaa16f747ca471b2bc`，C=`a70a85e7b6004e3cbe62f1db7d139379`，worker=`b2072f404c79`。

- 分工：检查接口、异常、算术、定义绑定和隐藏边界；不编辑（P#160）。子读取 `forth.py` / `forth_test.py`（C#56/#59），两次源码搜索（C#115/#118）；获取行号的复合 shell 被拒绝（C#112）。3 次模型、5 个工具、29,612 token；未运行测试或写文件。
- 证据：公开测试支持栈操作、异常消息、定义捕获与重定义语义。子把负数除法向零截断标成 canonical / likely hidden expectation，没有冒称公开测试直接验证了负数除法。
- 重复调查：父更早已读相同两份文件（P#171/#174），并在 P#259 自己说明“定义在创建时捕获引用词语义”。唯一实现 P#784 已使用 `abs(left) // abs(right)` 加符号恢复，完成向零截断。
- 交付：子 C#636 于 14:17:31.727 完成；父 14:17:48.004 写完后，P#798 才一次零等待 status 收取。全文首次进入 `requests/0008-7e560a0c8d3497c7c667201f7f1faa0736f8784c17a4517b99db1b03e2a630d5.json`。
- 实际接续：P#879 明确说“worker confirms … truncation toward zero”，随后尝试负数除法、定义捕获等边界 shell 检查（P#884），但因 `approval_unavailable` 没有执行。公开 pytest 已在 P#882 通过 54 项；交付后无源码修改，也没有落盘新增测试。可确认“确认了父已实现的语义并被引用”，不能确认“发现独立缺陷并促成修复”。

新版 Forth：P=`b22146fff33b4e7b996ff767dc759354`，没有子任务。初版数字 regex 过度转义、数字与指令序列返回类型不一致，先后由父通过失败反馈修正（P#745 → #854；#865 → #987 fresh-read 拒绝 → #1073；#1084 → #1227）。P#1238 起通过 54 项，其后 #1241/#1288 再跑相同公开套件；没有新边界测试。这解释了较多步骤的一部分，不能当成 Worker 本身的失败证据。

## REST API：都先重复阅读，再等待调查；实现差异不能直接归因

### handoff_only

Episode：`aider-python-rest-api--handoff_only`。P=`0829ebf3ec9842d7aa2009c5a6434c06`，C=`96a1c4be2d574cdd845217f894982a07`，worker=`fa9f8b6a0275`。

- 分工为接口、边界与实现建议（P#132）。子读实现、测试、说明（C#74/#77/#80），又用三个 `nl -ba` 命令重读（C#132/#135/#138）；4 次模型、8 个工具、37,674 token。报告对 JSON 字符串、排序、双向债务抵销有来源，未运行测试或写文件。
- 父在子读之前已完整读实现和测试（P#143/#146）。P#173 一次 300 秒等待取得报告；全文首次进入 `requests/0006-a4652f2617a42aeda0735dcbdd5fe2420a36a72b04ad2f105d956134815e780f.json`，然后 P#695 写首版。
- 首版通过公开 9 项后，父 P#878 自己指出同向重复借款会覆盖原有欠款，并经 P#998/#1898/#2473 修改。子报告主要建议“抵销相反方向债务”，没有明确指出或举例覆盖同向累加缺口，也未审阅父的新实现。因此该修复应归为父自查，不能记为子缺陷发现。
- 额外开销包括默认 unittest 发现零测试（P#718）、编辑失去新鲜读取后被拒绝（P#1420），以及被拒绝的重复借款 shell 检查（P#2509）。最终公开套件再次通过（P#2499），没有落盘新的重复借款测试。

### redesigned

Episode：`aider-python-rest-api--redesigned`。P=`4aa6436b9ff84b5bafab62459bac3b06`，C=`e2e0e504a02d40738f51351dff699fd9`，worker=`ab5bfa6f19e4`。

- 分工同样是接口、边界和建议，另要求测试命令（P#132）。子读实现、测试、说明（C#53/#56/#68），读测试尾部空区间（C#71），跑 pytest stub 基线 9 失败（C#141），获取行号的复合 shell 被拒绝（C#200）。5 次模型、8 个工具、50,684 token；没有写文件。
- 父仍更早完整读实现和测试（P#143/#146），P#173 一次 300 秒等待得到交付。全文首次进入 `requests/0006-e08d71a88cc3b4069211e9949d6e6fa2b8de988fed1e54949c40901f6ea486c9.json`。
- 子建议中明确说 lender 的 `owed_by` 与 borrower 的 `owes` 增加；父唯一实现 P#695 正确用既有同向债务加 amount，没有 handoff_only 的覆盖再修复过程。这是“报告与后续实现相符”的证据，但父已自行读过测试、根请求包含规格，父也未明确归因，不能证明子独立补足了遗漏或新调度造成了改善。
- 子已推荐 `pytest -q` 或显式 unittest 模块，父仍先运行默认 unittest 得到零测试（P#718），之后自纠正（P#794）。说明“报告被送入”不等于全部可执行建议被采用。后续 shell 边界检查被拒绝（P#807），P#873 重跑公开 pytest 9 项；没有新增测试。

## Django-11532

### handoff_only：共享入口建议正确，父最终采用局部修复

Episode：`swe-verified-django__django-11532--handoff_only`。P=`f41a7cd3b64247359ee61f72d9b75f88`，C=`30b5a400f8794c80ae6652fa67049cbe`，worker=`0f2d66d303f8`。

- 子检查 hostname / Message-ID / 测试，12 步耗尽后用最后一次无工具轮交付 `PARTIAL HANDOFF`；4 次模型、56,323 token。报告来源包括 `utils.py`、`message.py`、`tests/mail/tests.py`（C#148/#151/#154），以及相关搜索；标准库实验被审批拒绝（C#275），没有声称跑过测试。
- 报告明确建议在 `CachedDnsName.get_fqdn()` 缓存入口统一 IDNA，覆盖 `make_msgid()` 和 SMTP，另建议回归测试。父 P#237 一次等待取得部分报告，全文进入 `requests/0006-79bd3db190930c225a17e26545539570ae74d6749182831ee9f31747b98e0190.json`。这次预算收尾确实保住了一条可用的跨路径建议。
- 父在此前也读了相同核心来源和 SMTP（P#175/#178/#200/#206）。P#326 明确引用“worker confirms”并认可共享入口；但 P#585 随后改成仅在 `message.py` 的 `make_msgid()` 调用处编码，理由是兼容直接注入 `DNS_NAME`、保留 SMTP 的 raw FQDN。实际 P#589 只改调用点，P#779 添加回归测试，未落实子提出的共享入口方案。
- 独立评分的目标 F2P 已通过，整体仍失败。`grading/tests.log` 中 SMTP 用例的异常是 `smtplib` EHLO 发送 hostname 时的 `UnicodeEncodeError`，不是评分阶段 socket 不可用：最终 `utils.py:16` 仍缓存 raw `socket.getfqdn()`，`smtp.py:54` 仍将 `DNS_NAME.get_fqdn()` 用作 `local_hostname`。子建议正好覆盖该遗漏路径，但未被最终采用；不能把有价值建议与完成改进混为一谈。
- 执行期另有独立问题：Django 自定义测试命令被 `run_tests` 拒绝（P#846），父改走 shell 跑目标测试/45项 MailTests，再为严格完成门槛反复尝试 pytest、环境变量、`sitecustomize` 和临时 unittest 壳（P#1479—#2520）。首次尝试结束回复 P#1376 之后，仍有 16 次实际模型回复、382,452 个报告 token（按后续 `model_reply.usage` 求和）。执行期全量 mail 的 socket 限制与独立评分的 Unicode SMTP 错误是两种不同原因。
- 终态虽为 `provider_error`，P#2523 实际 category 是 `evaluation_budget`、message 是 `evaluation_whole_tree_model_budget`：全队 760,059 token 越过 750k 外部额度，非远端服务故障。最终留有 `formal_mail_test.py`，不能把暂存检查壳算成已清理的产品修复。37 次实际模型调用、61 个工具、606.2秒。

### solo：独立评分通过，但 Runtime 未正常收尾

Episode：`swe-verified-django__django-11532--solo`。P=`b071c288e9dc4164be3fd6a657a95060`，无子任务。

父同时修复 `utils.py` 中 hostname 缓存（P#553）和 `message.py` 调用点（P#670），另加回归测试（P#960）；独立 F2P 1/1、P2P 148/148 全部通过。父首次尝试结束（P#1950）后仍为正式验证门槛继续调用 plain unittest、设置环境变量（P#2032/#2130），最终遇到 900 秒外部墙钟截止，记录 `TimeoutError` 和 `stopped/cancelled`（P#2215）。任务正确性与 Runtime 正常结束在此明确不同，不能把超时直接写成代码任务未通过，也不能声称运行成功收尾。

### redesigned：委派丢失原始失败场景，父引用报告缩小修复范围

Episode：`swe-verified-django__django-11532--redesigned`。P=`7d275e102b3a49d4a071465399a68a87`，C=`a099b86460ad4fc8a2c84e8a7b6a01b5`，worker=`95f012393b64`。

- 父原始 user 请求明确含有非 ASCII 主机名（正宗 / 漢字）、`iso-8859-1`、`UnicodeEncodeError`、期望 punycode 与重现代码。P#174 的委派却仅要求调查 DNS_NAME、Message-ID/domain encoding，找最小修复点和回归模式，未提供失败输入、编码或错误。
- 子首次真实请求 `0001-c28a3a4079589dd12f3aa211761f046d1db8542f500b1fd57523c44967f7f905.json` 只有上述概括任务、预算与空 workflow 状态，没有原问题或父会话。此处是可直接确认的任务上下文遗漏，不是靠最终失败倒推。
- 子花费 24 个工具、6 次模型和 108,007 token 搜索/读取实现、测试、设置与标准库 `make_msgid()`，耗尽步骤后交付部分报告。报告明确说“exact defect … unresolved … needs the reported failing case”，既没有复现具体输入，也没有提出 IDNA 修复；只说若问题是传入对象，窄边界可能在 `message.py:260`。父早已具备的失败场景没有进入子任务，调查预算因而用在猜测缺陷是什么。
- 父 P#303 一次 300 秒 status 已取得部分报告，全文进入 `requests/0007-e7f8be4ca72c79ea66decdc71b654872d28dc49d94f94d6cda1c9d9e00717d1c.json`。P#381 明确引用“delegated inspection confirms SMTP relies on `CachedDnsName.get_fqdn()`”，据此认为修改缓存会扩大影响，决定只修 Message-ID 边界；P#508 实际只改 `message.py`。这是报告影响父决策的明确证据，但并不是独立发现完整修复方案的证据。
- 父首个测试补丁因匹配文本不唯一被拒绝（P#697），重新读取后 P#947 成功新增原问题的非 ASCII hostname + `iso-8859-1` 回归。随后 P#953 收到供应商 `incomplete_response` / `responses:failed`，无输出、无 usage；Runtime 因此 failed/provider_error，尚未运行父方测试。这与旧臂的外部预算耗尽不同，也不能假定若模型继续就不会发现遗漏。
- 独立评分目标 F2P 1/1 通过，P2P 129/148，19 个要求项 ERROR，整体失败。原始评分日志同样显示 SMTP EHLO hostname 的 `UnicodeEncodeError`，最终 `utils.py:16` 仍为 `self._fqdn = socket.getfqdn()`。可确认报告影响了当前错误修复范围，但不能把供应商中断后的未知后续行为视为已发生。18 次模型请求、46 个工具、报告 285,157 token、758.8 秒。

## 当前可支持与不可支持的说法

三题合计五个实际子任务：3 个 completed、2 个 step_limit 部分交付，五份交付全文均进入父模型。每个子任务只调用一次 status，合计 5 次，均直接取得终态，没有忙轮询。合计 22 次子模型请求、57 个子工具、282,300 个报告 token；全部只读，没有写 Worker、续问、恢复或自动投递事件。两次预算收尾确实保住了部分交付，但其内容质量不同：旧 Django 给出正确的共享修复入口，新 Django 因缺少失败场景未能确定缺陷。

按每个子任务分别去重，子通过 `read_file` 直接读取的路径共 19 个，其中 12 个父在取得报告前也已直接读取；另有两份 REST API 说明已包含在根任务规格中。此计数仅表示文件级调查重叠，未把同文件不同区间当成完全重复，也未声称其余读取均有独立价值。

九次独立代码评分通过 7 次：前两题六次全通过，Django 仅 solo 通过。Django 的三种 Runtime 终止须分别理解：solo 正确代码但墙钟超时；旧臂代码遗漏加外部 token 额度终止；新版代码遗漏加远端响应中断。不能把 Runtime 终态替代代码正确性，也不能把较低消耗自动写成协作效率提升。

目前可支持“报告能进入父请求，部分建议被引用，步骤耗尽仍可交付”；尚不能支持“这三题的子任务提升通过率”或“共享预算/自动交付造成时间与 token 改善”。后续完整样本应继续核对委派是否传递关键复现条件、是否提供父未掌握的证据，以及父是否落实了建议；本审计不据此改动冻结中的实现或协议。
