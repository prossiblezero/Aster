# Django 16256：配对复验的实现与交付审计

状态：2026-09-29，repeat-01/02/03 的 Solo 与 Worker 共六条均已完成审计。只读既有结果、请求、轨迹及最终源码；没有额外模型请求、诊断测试或冻结代码/协议修改。

原始证据根为 `artifacts/private/worker-focused-20260929/repeat-XX/`，episode 为 `swe-verified-django__django-16256--solo` 或 `--worker`。结果与独立日志在 `runs/<episode>/{result.json,grading/tests.log}`；模型轨迹在 `workspaces/<episode>/.aster/runs/<run_id>/trace.jsonl`。P/C 表示父/子，`#` 是对应运行的 trace seq。F2P/P2P 数量按独立评分要求项计数，未叠加日志解析产生的别名。

## 已完成结果

| 重复 | 臂 | F2P | P2P | Runtime | Worker完成/启动 | 全队模型/工具 | 报告token | 秒数 |
|---|---|---:|---:|---|---:|---:|---:|---:|
| 01 | Solo | 0/9 | 53/53 | completed/verified | 0/0 | 11/29 | 276,365 | 219.9 |
| 01 | Worker | 0/9 | 53/53 | completed/verified | 0/1 | 26/56 | 472,508 | 483.8 |
| 02 | Solo | 6/9 | 53/53 | completed/verified | 0/0 | 15/36 | 387,918 | 360.3 |
| 02 | Worker | 6/9 | 53/53 | completed/verified | 0/1 | 24/54 | 454,179 | 447.5 |
| 03 | Solo | 6/9 | 53/53 | completed/verified | 0/0 | 10/31 | 187,614 | 224.6 |
| 03 | Worker | 6/9 | 53/53 | completed/verified | 1/1 | 22/51 | 435,767 | 381.0 |

这道题要求为 related manager 的 `acreate()`、`aget_or_create()`、`aupdate_or_create()` 提供正确异步入口，保留同步 manager 注入的关系参数及中间表更新。独立 F2P 包含普通 M2M、反向 FK 与 GenericRelation；只修题面示例中的一个方法或一个文件不足以满足九个要求项。

## repeat-01 Solo：已实现但新增方法不能运行，另有方法与关系类型遗漏

P=`15046a76c6c8406b80e32ac9641846db`；source commit=`856345eab891b327d61466e6c27b79d908be6b98`。

- 父读取普通反向 FK 与 M2M manager 的同步方法（P#73/#76），以及既有 async QuerySet 测试（P#79）。P#403 的决策只提 `acreate()`，尽管根请求标题同时列出三个方法。
- 唯一源码补丁 P#407 在 `django/db/models/fields/related_descriptors.py` 的两个 manager 工厂增加 `acreate()` 并调用 `sync_to_async(self.create)`，但未补 `sync_to_async` import。最终文件只在两处新调用出现该符号（797/1200行）；独立 `test_acreate` / `test_acreate_reverse` 明确因 `NameError: name 'sync_to_async' is not defined` 报错。这是新增代码不可执行，不能笼统归为测试环境失败或“仅 GenericRelation 未支持”。
- `aget_or_create()` / `aupdate_or_create()` 的 manager wrapper 未实现。父后续专门搜索并阅读 QuerySet 对应方法（P#551/#554/#557/#560），却没有第二次修改；独立普通/反向四项均为 `AssertionError: 0 != 1`，即对象创建未进入预期关系。
- `django/contrib/contenttypes/fields.py` 没有被修改；最终 GenericRelatedObjectManager 仍只有同步 `create/get_or_create/update_or_create`。独立 generic 三项经 QuerySet 路径失败，日志为 `generic_relations_taggeditem.content_type_id` 的 NOT NULL `IntegrityError`。其关系参数注入被绕过，属于另一处范围遗漏。
- 父没有新增回归测试，只运行旧公开套件 `async`（P#506，36通过/1跳过）及 `async get_or_create many_to_many`（P#697，111通过/4跳过），两次工具实际成功。最终口头报告“115 tests passed, 4 skipped”把总数当成通过数；原生 hook 的 `passed_tests=111` 记录正确。P#700 compileall 也成功，但编译不会解析运行时缺失符号。
- Runtime 因有效公开测试证据正常 completed/verified（P#837），独立评分仍 F2P0/9、P2P53/53。因此完成门槛成功与任务正确性在此不同：原生 runner 修复了可执行验证入口，不保证模型选择的测试覆盖新行为。该运行没有 Worker，不存在委派贡献可归因。

## repeat-01 Worker：报告及时提供前置信息，但父重复调查且未验证新增路径

P=`7bb76029e21e40388abf07e21cc7872c`，C=`589879c81a1146d2a4307e4a738e7ef7`，Worker=`19faf6db6f92`。子为 `stopped/step_limit` 的部分交付，`workers_completed=0`，不能将父 completed 误写成子完整完成。

- P#171 分派只读调查：检查两个普通 related-manager 工厂、三个方法和测试标签。`parent_work` 是“inspect the implementation structure and integrate the fix”，与子调查实现语义部分重叠。父收报告前只做了目录、diff 和工具加载；随后 P#240 一次 status 等待，期间没有独立实现或检查。子在 10:45:20.758 交付，父在 10:45:27.826 取得。
- 原请求背景已实际进入子首请求，包括三个方法标题及 `from asgiref.sync import sync_to_async` 示例。本轮没有前次评测所见的原始问题丢失，不能用传递机制解释这次漏 import。子报告全文首次进入父 `requests/0004-5d0eb91205e4a6e7ef478e702ade7efc2bd997eb72bac19cd4c609184d702711.json`。
- 子用了 24 个工具、5 次模型、96,819 token，读了 7 个唯一文件，没有写入或运行测试。它明确指出需给两个 manager 工厂各加三个 wrapper，保留 `through_defaults` 与 `alters_data`；还读了 `tests/generic_relations/tests.py`（C#214/#241），建议父增加 reverse FK、forward/reverse M2M 与 generic 的异步回归。报告没有检查 GenericRelatedObjectManager 实现或提出给它补 wrapper，也没有单列缺失 import。
- 正面证据：父 P#303 明确以“investigation confirms”引用报告，之后才首次读源码，报告的具体方法清单和测试位置为前置信息。限制是父仍直接重读子 7 个来源中的 5 个：相关 descriptor、QuerySet、async测试/模型、get_or_create模型；并额外检查 `manager.py`。这是文件级重叠，不表示每段阅读完全重复。
- 报告未一次性解决父决策：父 P#537 曾重新断言只有 `acreate()` 缺 override，尽管子已说需六个方法；P#540/#567/#592 再查 Manager 方法生成后，P#1110 才自行确认三个方法均绕过 manager，并在 P#1114 添加六个 wrapper。后续仅 P#1452 改签名换行。报告与最终覆盖相符，但不能隔离证明其节省了父推理步骤。
- 最终 `related_descriptors.py` 的普通/反向六个 wrapper 均调用未导入的 `sync_to_async`（797/810/826/1216/1239/1263行）。独立日志有六个相应 `NameError`；GenericRelation 文件未修改，另三个目标仍是 `content_type_id` NOT NULL `IntegrityError`。与 Solo 相比，多加四个 wrapper 把四项未绑定关系的 FAILED 变成了缺符号的 ERROR，没有新增通过项。
- 父未采纳子提出的新回归/generic覆盖，只运行已有 `async.test_async_queryset get_or_create` 两次（P#1231/#1539），实际每次 69通过/3跳过，`passed_tests=69`。最终口头“72 passed, 3 skipped”再次把总数误报为通过数。compileall（P#1311/#1553）成功仍不能发现缺 import。
- 全队 472,508 token，其中子约20.5%；父自身375,689已高于 Solo 全队276,365。两臂同为 F2P0/9、P2P53/53；Worker 多196,143报告token（约71.0%）、15模型调用、27工具、263.9秒。本次无provider/额度/超时终止，差异可描述为这一次配对的实测额外消耗，但不能仅凭单次归因成稳定成本结论。

## 第一对覆盖对照

| 行为 | Solo最终文件/独立结果 | Worker最终文件/独立结果 |
|---|---|---|
| 普通M2M及反向FK的acreate | 两个wrapper已加，缺import；2 ERROR | 同样缺import；2 ERROR |
| 普通M2M及反向FK的aget_or_create/aupdate_or_create | wrapper未加，关系数量0；4 FAILED | 四个wrapper已加，缺import；4 ERROR |
| GenericRelation三个async方法 | 未修；3 ERROR | 未修；3 ERROR |
| 新增针对性回归 | 无 | 子建议但父未写 |
| 原生runner正式证据 | 111非跳过项，通过 | 69非跳过项，通过 |

## repeat-02 Worker：普通关系实现正确，关键 import 由父纠正子报告

P=`f9f41693e8534fb1a2f10f8b8218e6e2`，C=`b3d8bf3ca95c409aa2fecb97e6e27e05`，Worker=`cd0a39692dee`。子仍是 `stopped/step_limit`，24工具、6模型、130,990 token；无编辑、无测试。

- P#194 将两个普通工厂的同步/异步行为、方法和测试调查交给子，父在 P#269 一次 status 等待。子 10:53:57.229 交付、父 10:54:04.212 收到；全文进入父 `requests/0005-1fa043d2bdf85b3af57e83d7aff39de4025e474497ca7015bc60b2124ec2068c.json`。父此前做了目录、async测试搜索、diff，尚未直接读实现；报告提供了前置源码解释，但未与父独立编码并行。
- 子正确区分反向 FK manager 与共享正/反向 M2M 工厂，建议六个 wrapper。父 P#396 引用“investigation confirms”，后续覆盖与该建议一致。不过父接着直接重读子7个来源中的6个，未体现减少源码调查；两者也都未调查 `django/contrib/contenttypes/fields.py` 的 generic manager。
- 报告存在具体错误：“`sync_to_async` is already imported in the module”。子 C#89 只读 descriptor 的1–35行，尚未覆盖真实 import 区域；最终结论没有该 import 的实际源码证据。父 P#405 读1–85行，P#926 明确指出 import 缺失，再于 P#930 同时补 import 和六个 wrapper。这是父核查纠正子报告，不能将这次相较 r1 的关键修复记为子独立发现。
- 最终文件66行确有 import，普通/反向六个方法均委派相应同步 manager 方法。独立六项全部 PASSED；GenericRelatedObjectManager 未修改，剩余三项仍是 `content_type_id` NOT NULL `IntegrityError`。父 P#1040 口头说“all nine wrappers”，最终源码实际只有两个工厂的六个 wrapper，不应据此认定 generic 已覆盖。
- 子建议补 async 关系成员/count及 created flag 回归，父没有新增测试。P#1043 跑 `async get_or_create`：81通过/3跳过。P#1085 使用不存在的 `update_or_create` 顶层label导致收集错误，P#1174 搜索后改用 `get_or_create.tests.UpdateOrCreateTests`，P#1189通过18项。直接检查 async 方法/marker 的 shell（P#1430）被拒绝，不能算已执行异步关系行为测试；不过独立评分实际确认了普通六项正确。
- 全队454,179报告token，其中子28.8%，父323,189；24模型/54工具、447.5秒。相较上一条 Worker，成功覆盖从0/9变成6/9的直接代码证据是父补齐缺失 import，仍不能据两个不同生成样本断言协作机制提高了通过率。同轮 Solo 最终文件与它逐字节相同，详见下文。

## repeat-02 Solo：自行补 import，尝试新增回归失败后放弃

P=`05e74692f7ae4337a8e8bb61cc62d3b2`。P#748 先加普通两个工厂的六个 wrapper，既有 async/get_or_create 测试通过后，P#1183 自行注意到缺少 import，P#1187 补齐。与 r2 Worker 相同，最终普通六项通过、Generic 三项 NOT NULL，P2P53/53。

P#843/#953/#956 的精细测试标签均收集失败，父转用完整 `get_or_create` app（P#1051）。P#1705 明确提出增加 reverse FK 三个异步方法的回归，但 P#1708 补丁因 `patch_overlap` 被拒绝，父 P#1762 将其称为 optional 后放弃；最终没有测试文件修改。import 修复后 P#1765/#1768 重跑的既有套件分别36通过/1跳过、45通过/2跳过。它没有 Worker，说明同样普通覆盖可以由单 Agent 达成，也说明缺乏新回归并不只发生在委派臂。

## repeat-03 Solo：识别了 GenericRelation，却主动排除修复

P=`075ac98110144112b89666a9f7178e2c`。父 P#155 说要检查 generic，P#158/#161 搜索其同步方法，P#249 却以“Generic relations have separate code and are outside the supplied change”为由排除；P#258 后续确实读了 `django/contrib/contenttypes/fields.py:720–780`，仍未改它。这条遗漏是明确的范围判断错误，不能说父根本没找到 generic 实现。

P#783 一次补齐普通六个 wrapper及 import；独立普通6/6、generic0/3。父没有新增测试，P#890/#902/#905/#908 仅跑已有 async、get_or_create、m2m_through、many_to_one 套件，分别36/45/53/38非跳过项通过；最终答复又把前两组总数当成通过数。结果只有 descriptor 一个文件修改，任务整体失败但 Runtime 正常完成。

## repeat-03 Worker：完整交付晚于实现，错误标签造成明确额外工作

P=`64f2e2305e6848d3bcbbe7314cc818a0`，C=`fafd1d7db1444e6b9e039eb79f670ee5`，Worker=`075e7d277244`。这次子确为 `completed/verified`，23工具、7模型、180,690 token，不是 budget partial；只读任务不要求测试，因此其 verification 仍 missing 并不矛盾。

- P#184 派出同类语义/测试调查后，父马上在同一 descriptor 与 async 测试中搜索和阅读（P#200/#203/#206/#270/#273/#276），与子工作重叠。子6个唯一直接读取文件中，3个父在交付前也直接读取：descriptor、async模型、async QuerySet测试；子另读 QuerySet、同步测试和 generic测试，但未调查 generic实现。
- 父 P#292 读 import 区域，P#819 自行识别缺 import，P#823 于11:11:19.589 已一次写完六个 wrapper及 import。子11:12:23.945才完成，父P#848在11:12:31.223取得报告；全文首次进入 `requests/0008-d1c2de2d0ed5dcdf5bd601e0d7dd34c697d7b5f304b09adc4824774d33f30f98.json`。此后没有代码修改，所以不能把同样方法建议认作促成这次实现的证据。
- 报告的具体测试建议有错误：虽已读正确文件 `tests/async/test_async_queryset.py`，却建议 `async.tests.AsyncQuerySetTest`；另把 `UpdateOrCreateTests` 的四个方法归到 `GetOrCreateTests`。父照用前者 P#967 得到 `async.tests` 无该类的收集错误，照用后者 P#1180 得到四项不存在方法的错误；P#1278 明确说“stale method names from the worker’s inspection notes”。这是比“报告被读到”更强的采用证据，但效果是引入额外失败与纠正。
- 父纠正标签后 P#1094 运行27通过/1跳过，P#1281完整get_or_create为45通过/2跳过。未新增异步关系回归；独立普通6/6、Generic0/3、P2P53/53。全队435,767token，其中子41.5%，父255,077；没有通过率收益可以归于这次完整交付。

## 三轮覆盖、成本与可支持结论

每一对的独立结果都相同：r1为0/9，r2/r3均为6/9；全部P2P53/53。两臂整题通过率均0/3，F2P要求项合计均12/27。这里是同一道题的三次生成，不是三个独立benchmark任务，不能将要求项合计包装成广泛任务通过率。

更强的产物证据：r2两臂最终 `related_descriptors.py` 的SHA-256均为 `d355bd41e9c31994c4801711509e7fb09d4c4a72aa9166e3abbdcee9406192f4`；r3均为 `47cdec3cfd0f70d70b1bff4bca2aeff2ee2a3e8f3890eb9ae74ad4d679b9762f`。这两对唯一实际修改文件逐字节相同，GenericRelation 全部未修。

| 三轮合计 | Solo | Worker | Worker额外消耗 |
|---|---:|---:|---:|
| 报告token | 851,897 | 1,362,454 | +510,557（59.9%） |
| 运行秒数 | 804.8 | 1,312.3 | +507.4（63.1%） |
| 模型调用 | 36 | 72 | +36 |
| 工具调用 | 96 | 161 | +65 |

三个子任务合计18模型调用、71工具、408,499token（Worker全队30.0%）：两次step_limit部分交付、一次完整交付，三份全文都进入实际父请求，均一次status收取，没有忙轮询、续问、写Worker或子执行测试。按每个子分别去重，20个直接读取文件中14个父也读取；前两轮为收报告后重读，第三轮3个核心来源在交付前已读。此文件级重叠不等同于行级完全重复。

可支持：原始任务自动传递与有界交付机制在这三次都能工作；前两轮报告在父读实现前提供材料；第三轮完整报告中的错误测试建议实际影响了父动作。不能支持：这道题上委派提高任务通过率、扩大最终覆盖、减少成本，或完整报告意味着更可靠。三次Worker的token和时间均高于配对Solo；全队额外开销、重复调查和错误标签都有轨迹证据，但三个重复仍不足以估计其他任务的平均收益。

所有六次 Runtime 都正常completed/verified，无供应商中断、超时或预算终止来混淆父终态。正式测试工具确实运行了非空既有套件并准确记录跳过数；任务失败来自修复/覆盖不足。严谨的结论是：本题暂无委派质量收益，GenericRelation范围遗漏跨两臂稳定存在，需与“Worker交付成功”分开记录。
