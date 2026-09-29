# OpenLibrary authors：实现、查询语义与交付审计

状态：2026-09-29，repeat-01/02/03 的 Solo/Worker 六条均已完成审计。只读取既有结果、请求、轨迹、最终源码及冻结预算实现；没有执行测试、诊断或模型请求，没有修改冻结源码、数据或协议。

原始证据根为 `artifacts/private/worker-focused-20260929/repeat-XX/`；episode 为 `swe-pro-openlibrary-authors--solo` / `--worker`。独立结果和日志位于 `runs/<episode>/{result.json,grading/tests.log}`，轨迹在 `workspaces/<episode>/.aster/runs/<run_id>/trace.jsonl`。P/C 的 `#` 引用各自 trace seq。共同源码为 `856345eab891b327d61466e6c27b79d908be6b98`。

## 已完成配对结果

| 重复/臂 | 冻结 F2P | 冻结 P2P | Runtime | 正确且正常交付 | 全队模型/工具 | 报告 token | 秒数 |
|---|---:|---:|---|---|---:|---:|---:|
| 01 Solo | 4/4 | 4/4 | completed/verified | 是 | 22/44 | 556,691 | 335.5 |
| 01 Worker | 4/4 | 4/4 | stopped/team_token_limit | 否，final 为空 | 40/72 | 937,817 | 609.1 |
| 02 Solo | 0/4 | 2/4 | completed/verified | 否，独立评分失败 | 24/48 | 609,040 | 306.2 |
| 02 Worker | 4/4 | 4/4 | completed/verified | 是 | 41/71 | 891,322 | 383.4 |
| 03 Solo | 3/4 | 4/4 | completed/verified | 否，独立评分失败 | 18/47 | 423,279 | 255.8 |
| 03 Worker | 2/4 | 4/4 | completed/verified | 否，独立评分失败 | 39/66 | 830,914 | 366.8 |

r1两臂独立日志均为28项通过，其中冻结评分要求为8项；不将额外20项计入冻结分母。均没有 provider error，HTTP 请求全有 usage。r1 Worker 有一个只读子任务，`stopped/step_limit`，6模型/24工具、115,718 token，无编辑、无执行测试；其余父为34模型/48工具、822,099 token。`workers_completed=0` 不等于没有获得报告。

## repeat-01：冻结八项与最终代码覆盖

| 冻结要求项（TestImportAuthor） | 分组 | Solo | Worker |
|---|---|---|---|
| test_author_match_is_case_insensitive_for_names | F2P：查询语义 | PASSED | PASSED |
| test_author_match_allows_wildcards_for_matching | F2P：查询语义 | PASSED | PASSED |
| test_second_match_priority_alternate_names_and_dates | F2P：作者解析优先级 | PASSED | PASSED |
| test_last_match_on_surname_and_dates | F2P：作者解析优先级 | PASSED | PASSED |
| test_author_wildcard_match_with_no_matches_creates_author_with_wildcard | P2P：无匹配保留输入 | PASSED | PASSED |
| test_first_match_priority_name_and_dates | P2P：作者解析优先级 | PASSED | PASSED |
| test_non_matching_birth_death_creates_new_author | P2P：日期拒绝/新候选 | PASSED | PASSED |
| test_last_match_on_surname_and_dates_and_dates_are_required | P2P：姓氏日期门槛 | PASSED | PASSED |

两臂最终都只改 `openlibrary/catalog/add_book/load_book.py` 与 `openlibrary/mocks/mock_infobase.py`，没有测试文件修改。这里没有 Django 那种整块实现被遗漏的证据；作者解析优先级和查询语义两类均有代码修改和独立通过项支持。

- Solo 最终 `load_book.py:135` 的 `find_author(author, field, pattern)` 用 `field~` 查询；`:185` 的 `find_entity` 先查姓名，缺任一日期则按数字 key 选姓名候选，有两个日期时依次按姓名、别名、姓氏查找精确年份匹配。`:161` 明确要求输入能解析出年份，`:179` 按 `key_int` 选候选。无匹配仍通过既有 `import_author` 创建候选并保留字段。
- Worker 最终 `load_book.py:149` 同样用 `field~`，同时保留字符串入参兼容，并在查询返回时按 `key_int` 排序。`:199` 的解析先查姓名，之后查别名、姓氏；`:215` 将后两条路径限制为输入有两个日期，`:191` 要求它们精确匹配。最后 P#3090 加 `check_dates=has_both_dates`，使缺一个日期时姓名匹配不再被剩余日期拒绝。
- 两臂 `MockSite.regex_ilike` 分别在最终 mock 文件`:187` / `:188`：先去掉模式中的 `_`，将 `*` 转为 `.*`，其余字符 `re.escape`，最后 `re.fullmatch(..., re.IGNORECASE)`；`filter_index` 的 `~` 实际调用该方法。因此全字符串、大小写、通配符和下划线处理均已实现。冻结8项只直接给出大小写/通配符等端到端通过证据，不将代码存在当成所有边界均被独立测试过。

两种实现不是逐字节相同：Solo 在两个日期齐全时只接受能解析且精确匹配的候选；Worker 姓名阶段的 `_dates_match` 允许候选缺日期，并优先从姓名候选中挑精确年份匹配，别名/姓氏则仍有精确门槛。Worker `_exact_dates_match` 对不可解析的非空日期还会比较 `None == None`。这些是静态语义差异，本次没有追加边界测试，也不据此修改冻结八项得分或宣称额外失败；对未被八项覆盖的输入保持证据限制。

## repeat-01 Solo：直接实现两层，正常完成

P=`29511fbf95b34a108e70b4f2c3c7bf4b`。父先读 resolver、mock 和相关测试（P#115/#118/#121/#124），自行查既有日期函数和生产查询实现。P#601 实现 mock 查询，P#1123 增加 resolver 查询与年份 helper，P#1721 的整块补丁因 `patch_nonunique` 被拒，重读后 P#2272 成功完成优先级逻辑。

P#2283 运行既有导入测试，P#2786 扩大为整个 `openlibrary/catalog/add_book/tests`，实际139通过/1 xfailed；P#2286 编译成功。P#2710 自编的直接行为 smoke shell 被拒，不能计为已验证；最终没有新增回归文件。父最终报告正确列出已运行测试，运行正常 `completed/verified`，独立冻结8项也全部通过。

## repeat-01 Worker：有并行和自动送达，但独立贡献无法隔离

P=`1e4241d99d814652bf064b07b44f83af`，C=`8c7c69f489424a658b3d7d718797cbca`，Worker=`4f1304e13b35`。

- P#222 分派 resolver/测试/数据 API 的只读调查，声明父负责 mock 与测试执行。父在子运行期间读 mock 和 conftest（P#296/#299），确有时间重叠，没有 status 忙轮询或同步等候。一次工具参数格式错误导致 P#284/#285 整批未执行，父随后纠正。
- 子实际直接读7个唯一文件，除分派的 resolver、测试、conftest 和 utils，还读父声明负责的 `mock_infobase.py` 与 mock 测试（C#205/#208）。子报告正确指出原实现没有别名/姓氏路径、日期函数宽松、mock仅支持大小写敏感前缀、现有测试不覆盖新解析行为，并提出10类测试。它没有修改文件或运行验证。
- 子在12:03:57.352到24工具上限后部分交付；P#333于12:04:02.068自动投递，报告全文实际进入父 `requests/0009-9d85322840c1dcc1637dc19073dcc5bc1c96e0823b5f3a8c428d89dcb4025de9.json`。这是模型写出的部分报告，不是只有结构化终态的确定性交接。没有 `worker_status` 或续问；自动交付确实有效。
- 报告到达前，父已读子7个文件中的mock、conftest；到达后又直接读resolver、utils、mock测试（P#361/#390/#419/#438）。最终5/7文件被两者直接读取，这是文件级重叠而不是声称全部行重复。父#818对问题的描述和后续修改与报告一致，但父也独立重读了依据，轨迹没有明确引用子发现并省去父调查的动作，不能隔离出子独有的正确性贡献。
- P#822父实现mock；P#1344实现resolver；P#1648纠正别名参数和冗余调用；P#3090补齐部分日期时只按姓名匹配。子已经提出“缺任一日期用name-only”，父仍在初次实现中漏掉，到临近结束才自行补上（P#2914）。因此报告提过某个要求不能直接当成该要求已被正确采纳的证据。
- 子提出新增回归，父没有落地测试文件。父P#1724的既有24项全部通过；P#2262直接行为smoke被拒，P#2335较小regex断言实际成功。P#2454/#2457扩大到catalog与mocks，139通过/1 xfailed、7通过。清理unused import后P#2668重跑24项，最后日期修复后P#3138再次24项通过。多次测试并非全部无原因重复，后两次分别由源码变化触发；但它们仍主要是旧公开覆盖。

## repeat-01：为什么通过独立评分却没有正常交付

停止点发生在最后一次正式测试之后。P#3138于12:11:23.486记录24项通过，verification匹配最终workspace hash；P#3140准备下一个模型请求，输入估计为54,799 token。冻结 `runtime.py:552` 用估计输入加 `effective_output_tokens=8,192` 预留本次请求，得到62,991。

父checkpoint中团队 `charged_tokens=937,817`，正好等于所有40个HTTP请求报告的token总和。冻结 `workers.py:81` 的准入条件检查 `937,817 + 62,991 = 1,000,808 > 1,000,000`，因此拒绝第35个父模型请求，P#3142停止为 `team_token_limit`。该请求已保存payload但没有provider attempt，父只实际请求了34次，`final`为空。内部估计可以在外部报告总量尚未满百万时停止，协议已明确这种行为适用于两臂。

这是预算准入导致的未交付，不能写成测试失败、provider故障、超时，或子报告未送达。也不能假设被拒请求本来一定会输出最终答复；唯一可确认的是父已完成最后验证，下一请求未发出。子消耗占团队12.3%，父自身822,099也已比Solo556,691多47.7%；额外开销不能全部归于子模型的直接计费。

## repeat-01：可支持结论

r1独立任务通过率相同，但正确正常交付为Solo成功、Worker失败。Worker额外381,126报告token（+68.5%）、273.7秒（+81.6%）、18模型调用、28工具调用。相比Solo，父侧本身多12模型/4工具，另加子6模型/24工具。

正面机制证据是任务背景进入子请求、父子有并行工作、完整的部分报告自动送入父实际请求；负面实测是两层实现覆盖没有新增收益，调查重叠且团队在最终回复前用尽预算。只凭这一对无法估计稳定平均效果；不能将“已送达的有用资料”写成委派提高通过率或降低成本，也不将本次正确补丁计为正常交付。

## repeat-02 Solo：已实现但运行/查询错误，不是整块需求遗漏

P=`3ce263fd4a454ae3b25439fc2a52e57b`。两文件均被修改；P#730/P#1765实现mock ILIKE，P#1252实现姓名、别名、姓氏三个路径及年份过滤，P#2267恢复非person分支。最终独立评分仅2/8：无匹配通配符保留、姓氏缺日期拒绝这两个P2P通过；另六项失败。

| 独立失败项 | 已有实现与失败原因 | 分类 |
|---|---|---|
| case_insensitive、wildcards、first_priority、non_matching_dates、alternate_priority 共5项 | 五条独立traceback均在新增 `_walk_author_redirects` 的 `author['type']['key']` 失败 | 已实现但访问API错误 |
| surname_priority 1项 | 使用 `surname` 独立字段查询，已有作者只有 `name`，无结果后创建新dict | 已实现但查询语义错误 |

第一组的具体回归来自 P#1252：原来先用 `a.type.key` 判断是否存在redirect，只有发现非author类型才调用redirect walker；重构后对每个命中作者直接调用 `_walk_author_redirects`，最终 `load_book.py:159` 无条件执行 `author['type']['key']`。这里 `type` 是懒加载Thing，对其用 `['key']` 会经 `client.py:846/834/809` 加载 `/type/author` 内容；独立环境的该内容type是字符串，`:815` 再取 `.key` 抛 `AttributeError: 'str' object has no attribute 'key'`。日志明确包含该共同堆栈，属于新访问路径触发的运行错误，不是“大小写没实现”或“独立测试环境坏了”。同一基线中保留前置检查的Worker正常通过。

姓氏项是另一个可定位的错误：最终 `load_book.py:217` 调用 `_find_by_field(author, 'surname', author['name'])`，`:185` 直接查 `{'type': '/type/author', 'surname': <完整姓名>}`。独立样例保存 `name='William Brewer'`、输入 `name='Mr. William H. brewer'` 和匹配日期；记录没有独立surname索引。实现既未从输入取姓氏，也未对已有name做后缀查询，最终返回新候选dict，测试访问 `.key` 失败。父P#2303后来搜索过surname，但没有修改这条路径。不能把一个存在却查错字段的分支计为遗漏。

父未新增针对性测试。P#1851既有test_load_book通过；P#1864的直接行为smoke shell被拒。P#2503最终公开套件是65通过/1 xfailed，编译也成功，因此Runtime正常completed/verified；它们没有覆盖新的命中作者访问路径。最终答复宣称保留redirect行为与实现surname匹配，独立证据不支持这两项成功。

## repeat-02 Worker：这次结果更好，关键正确实现早于子报告

P=`d091c75203a347e88751792af32c9f02`，C=`a5ab7d72e94e48a8aa91e18be621510a`，Worker=`7bcb45078d3c`。全队冻结8/8通过且正常交付；子依然是24工具上限的部分交付，8模型/24工具、131,444 token，无编辑或测试。父33模型/47工具、759,878 token。

- P#134分派resolver与相关测试调查，声明父负责布局/mock；父P#216/#222即读子负责的resolver/test_load_book，之后又读utils与mock。子6个唯一成功直接读取文件中4个父也读过，且这4个父读取都在报告到达前。子同时也读父声明负责的mock。P#261一次1秒status只返回running，并没有获取发现。
- 子于12:14:02.638结束；父在本轮模型处理/补丁期间没有消费报告，到P#1386（12:14:23.326）才自动送达，首次实际进入 `requests/0013-a1a7dbb00a0661e638557dd22a8d18a6c10a1024d8b0cf87dbfc8bfeadf3d2bd.json`。报告列出现有代码/测试和需求缺口，没有指出Solo后来出现的嵌套Thing解引用问题，也没有给出surname应从name提取再查name后缀的关键事实。
- 在此之前，父P#1384已经完成主resolver补丁：`_query_authors`保留原有 `if any(a.type.key != '/type/author' ...)` 再进入redirect处理的前置判断；姓氏路径先从输入name提取surname，再用 `'*' + surname` 查name。最终文件对应`:160`与`:201–205`。这正是避开Solo两类失败的具体差异，但其代码产生早于子报告进入请求，不能归因为子报告使父修好了这两个问题。
- 报告提供了 `openlibrary/tests/catalog/test_utils.py` 这一具体测试位置；父收报告后首个测试P#1429同时运行它和test_load_book，105通过。时间和路径一致可支持“资料可能影响测试选择”，但父没有明确说明采纳来源，也不能由此证明解决上述两个失败：该检查仍在错误年份转义尚未修正时通过，重点覆盖旧utils行为。
- 后续关键纠正均有父自己的发现/执行依据：P#1682修正新年份正则的过度转义；P#2004恢复逗号姓名兼容；P#2016扩大公开套件后发现edition去重两项回归，父P#2103明确诊断其来自mock等号匹配范围过宽，P#2526将新等号语义限于name/alternate_names并保留其他字段旧行为，P#2559恢复139通过/1 xfailed；P#2878按数字key排序返回候选。子报告没有提出这几个新补丁中的具体错误，不能替父追认成子发现。
- 最终P#2918为224通过/1 xfailed，独立冻结8项全部通过，正常final存在。没有新增测试文件，P#2021的regex smoke shell被拒；不将未执行检查记入验证。最终mock的`~`分支和`=`分支仍有不同匹配范围，未追加超出冻结8项的诊断，不据通过分数扩大为“全部查询边界均正确”。

## repeat-02：收益和归因边界

这一次配对确有独立质量差异：Worker 8/8、整题通过且正常交付，Solo 2/8、整题失败。应保留这一胜场，而不是因为无法因果归因就抹掉实测结果。它说明开启Worker的这次运行产出了更好的补丁；轨迹不支持更强的“子独立发现关键问题并指导父解决”的解释。

代价为额外282,282报告token（+46.3%）、77.2秒（+25.2%）、17模型调用、23工具调用。父自身比Solo多9模型但少1工具；其余为子8模型/24工具。不能只选这次胜场作为稳定委派收益，三次完整结果见文末。

## repeat-03 Solo：姓氏已提取，但查询模式无法匹配完整姓名

P=`b0440b8ffb1e4c1086818bd262f6d442`。独立评分7/8，仅 `test_last_match_on_surname_and_dates` 失败；另外7项，包括别名匹配，全部通过。父P#694/#697调查surname，P#1218明确指出没有持久化surname字段，须从输入姓名提取姓氏并查候选name。因此不是没有找到正确存储位置。

P#1222添加的最终 `load_book.py:229` 确实从输入末词提取surname，`:232` 却查询 `{'name~': surname}` 而没有前导 `*`。父自己在mock加入了full-string匹配，因此独立样例输入末词 `brewer` 不能匹配已有完整name `William Brewer`，仍返回新dict，测试访问 `.key` 失败。这是查询模式实现错误；日期过滤和surname分支都存在，不应归为整块遗漏。

父P#1418自行修正新年份regex的转义；P#2000只清理import与格式，没有再修surname模式。没有新增测试；最后P#2012的既有作者套件123通过/1 xfailed，P#2015相关upstream/model39通过，Runtime正常完成。最终答复声称支持surname fallback，独立证据仍显示该必需样例失败。

## repeat-03 Worker：子完整完成并验证基线，但父误解输入与候选字段

P=`c40cd98aa4a447c9a6ae05a6a6708e0a`，C=`01478fd3e7c045c6a0e1740a6ff695b6`，Worker=`6d259cd101f2`。子确实 `completed/verified`，22工具、8模型、120,137 token；这次不是step_limit部分交付，也不是因为只读无需测试而空的verification。C#282于12:28:52.408实际运行既有test_load_book与test_utils，105项通过；父首个源码补丁P#752直到12:29:49.727才执行，因此子验证的是修改前基线。

- 子于C#824（12:29:32.822）正常完成。P#408此前一次status仍为running；父P#754于12:29:50.522自动送达完整报告，全文进入 `requests/0014-f863cad39aafc860a07608daa71fa86dcb0fbea50cf76b74c10983acb65efaf6.json`，在父resolver补丁P#1275之前。
- 子直接读7个唯一文件；父交付前已直接读其中5个，包括resolver、test_load_book、utils、mock和test_add_book。报告提供实现现状、日期函数语义、105项基线证据与待补测试清单；父随后包含该测试slice运行修改后的109项，是修改后重新验证，不能把基线和修改后运行简单算作无意义重复。
- 但报告没有把关键数据映射讲清楚：应拿输入的 `name` 去匹配候选的 `alternate_names`；surname应从输入name提取，而非要求调用方另给字段。最终父 `load_book.py:232–240` 只循环输入 `author.get('alternate_names', [])`，`:242–249` 只在输入 `author.get('surname')` 为真时走surname。冻结样例只有name和两个日期，故直接跳过这两条路径：别名与surname两项均返回新dict，独立6/8。代码分支存在但触发条件/字段归属错误，属于已实现但错误的语义覆盖，不是完全没写这两类功能。
- 父P#2034尝试的smoke也人为给输入补了`alternate_names`/`surname`，与这两个错误假设一致；该shell被拒，不能计为验证或声称“新分支已经测过”。子报告指出现有公开测试稀疏并建议加回归，父仍没有新增测试文件；完整报告没有阻止这个需求误解。
- P#1562由父纠正新regex转义；P#2357仅把输入alternate_names的字符串规范成列表，并未加入输入name本身。P#2416最终公开套件228通过/1 xfailed，Runtime正常完成，独立仍有两项失败。子105项基线、父109/143/228项修改后测试都是真实执行，但这些既有套件没有证明新增两条匹配路径满足原请求。

r3 Worker比Solo少通过1个冻结要求项，整题均失败；Worker额外407,635报告token（+96.3%）、111.0秒（+43.4%）、21模型/19工具。子完整完成、测试成功且报告已读到，仍不等同于代码正确性收益。

## 三次总结果与实际贡献

| 三次合计 | Solo | Worker | 差异/解释 |
|---|---:|---:|---|
| 独立整题通过 | 1/3（r1） | 2/3（r1、r2） | Worker多一次独立通过 |
| 正确且正常交付 | 1/3（r1） | 1/3（r2） | 相同；Worker r1预算停止 |
| 冻结要求项通过 | 17/24 | 22/24 | 同一道题的重复要求项，不是24个独立任务 |
| 报告token | 1,589,010 | 2,660,053 | +1,071,043（67.4%） |
| 运行秒数 | 897.5 | 1,359.4 | +461.9（51.5%） |
| 模型调用 | 64 | 120 | +56 |
| 工具调用 | 139 | 209 | +70 |

按整题独立通过配对，Worker1胜0负2平；按正确正常交付，1胜1负1平。两臂都独立通过的成本配对子集只有r1，Worker token+68.5%、时间+81.6%；两臂都正确正常交付的配对子集为空，不能报告该条件下节省比例。三个重复不是三个独立benchmark问题，不能将2/3直接泛化为大样本通过率优势。

三个子任务合计22模型、70工具、367,299 token（全队13.8%）；两次部分交付、一次完整交付。三份全文均在父真实请求中出现，都是自动投递，r2/r3各一次status只看到了running；没有续问、写Worker或结果未送达。r3额外有一次真实的修改前基线验证。按每个子分别去重，20个直接读取文件中14个也由父读取；r2/r3重叠均在收到报告前就发生，文件级重叠不等于全部行内容重复。

父三次工具总数恰好也为139，与Solo一致；新增70个工具全部来自子，而父模型调用仍98次，比Solo64次多34次。父报告token共2,292,754，亦比Solo多703,744（44.3%）。因此本题开销不是只多算了子模型费用，协调后的父轨迹本身也更昂贵。

实际贡献能确认到：子独立调查、把原有实现和测试位置整理送达，以及r3执行并交付基线验证。无法确认到：子独有关键事实促成某项成功修复、减少父步骤，或避免需求错误。r2的正确性优势来自报告进入父请求前已完成的关键实现；r3报告虽先于resolver补丁到达，仍未阻止字段映射错误。这个结论不否认2/3对1/3的实测胜场，但它不足以支持“协作机制稳定提升任务成功且没有交付代价”的说法。

六条均保留原冻结8项评分；没有根据本次失败追加诊断、改变oracle或丢弃预算停止运行。源码中未被8项覆盖的语义差异仅作静态限制记录。最终可报告为：本题Worker独立补丁通过次数多一次，正常成功交付次数相同，总成本更高，关键独立协作贡献仍缺乏可归因证据。
