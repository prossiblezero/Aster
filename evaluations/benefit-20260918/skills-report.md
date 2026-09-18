# Skills 真实维护任务对照

**本轮四个维护任务都由 Aster 完成并通过独立浏览器验收；Skill 的正文与资源加载机制在日历任务中真实使用，但没有获得可以可靠归因于 Skill 的收益结论。** 备份 Skill 条件仅发现入口，没有加载正文；日历条件虽完整加载，却遇到不同的服务端模型标签和更多模型轮。不能将有无技能目录当作“实际使用技能”替代指标，也不能称这次是固定后端模型的严格因果实验。

## 条件与交付结果

四组均从通过基础验收的 `delivery-base-finish` 快照起步，按预定顺序运行各一次：备份普通文档、备份 Skill、日历 Skill、日历普通文档。模型实际编写 HTML/JS、测试和 README；操作员没有修改候选，没有返修提示或补样。Memory 关闭，Worker 关闭，禁网 required 沙箱，Responses 流式开启。两次不同功能扩展不串联，避免前一功能实现进入后一对照。

对四份 launch 的核对显示：产品源码、初始候选文件、公开规则文件和所有配置完全一致，源码冻结于 `dfe64ef50601b74acc75a7ebc8a309939c3eb38b`；区别是是否放置技能包。每对功能使用相同需求。Skill 的 `references/workflow.md` 与普通组可读的 `docs/workflow.md` 字节完全相同，领域协议也对所有组开放。技能包提供描述、入口与加载提示，没有独占隐藏答案。

| 条件 | Runtime | 独立浏览器验收 | 工具步 | 模型轮 | 运行秒数 | 非成功工具 |
|---|---|---:|---:|---:|---:|---|
| backup-docs | completed / verified | 23/23 | 35 | 16 | 250.27 | 3 error、1 blocked |
| backup-skill | completed / verified | 23/23 | 23 | 16 | 158.12 | 2 error |
| calendar-skill | completed / verified | 15/15 | 39 | 26 | 200.71 | 3 error |
| calendar-docs | completed / verified | 15/15 | 33 | 16 | 276.19 | 3 error |

备份检查覆盖实际下载、按 ID 替换/追加/保留、空包、恰好 1 MiB、越界与非法包整包拒绝、文本安全展示，并回归基础交互、刷新和异常存储。日历检查读取实际下载内容，覆盖 UTC 转换、30 分钟事件、稳定 UID、转义、UTF-8 字节折行、空清单和全部完成清单，同时回归基础功能。四组共有 **76/76 个检查执行通过**，包含重复的基础回归；它们是四个候选的验收项，不是 76 个独立开发任务。

模型自测与上述评分分开：backup-docs 的 11 个测试运行成功两次，中间一次保留 reporter 参数被拒；backup-skill 12 个测试成功一次；calendar-skill 11 个测试成功两次；calendar-docs 12 个测试成功一次。不以自测数量评定实现质量，也不以 `verified` 代替浏览器验收。

## Skill 到底有没有被用上

| 条件 | 发现 / 加载工具入口 | Skill 正文 | Skill 资源 | 普通 workflow 文档 |
|---|---|---|---|---|
| backup-docs | 无技能目录 | 0 | 0 | 成功读取 |
| backup-skill | 第 1 步 tool_load 加载 skill_load | **0** | **0** | 成功读取 |
| calendar-skill | 第 1 步 tool_load 加载 skill_load | 第 2 步成功，394 字符 | 第 3 步成功，283 字符 | 第 10 步又读取同一内容 |
| calendar-docs | 无技能目录 | 0 | 0 | 成功读取 |

`skill_load` 的 `resource` 参数负责资源读取，本轮没有另一个名为 `skill_read` 的调用。日历 Skill 组的正文和资源返回均为 `next_offset=null`，并标记 `untrusted=true`；不是只获取名称或截断片段。最终 `active_skills=["browser-maintenance"]`，备份 Skill 组则为空。

这说明新版 Skill 发现、正文和资源按需加载机制可用；也暴露出两条实际问题：**看到目录与加载工具入口不保证会加载正文；加载资源后仍可能重复读取同内容的普通文档。** 备份组工具步和输入减少是观察结果，不能写成“Skill 正文使其减少”，因为正文根本没有被加载。描述目录本身是否影响路径选择，本次单样本也无法分离。

## 用量、传输与模型标签

客户端请求始终指定 `gpt-5.6-sol`、128,000 总上下文、32,768 输出、180 秒单请求超时；每组上限 55 工具步、35 模型轮、1,200 秒，没有触及上限。**固定的是客户端请求配置，服务端返回标签不固定。**

| 条件 | 输入 tokens | 输出 tokens | 总 tokens | 缓存读取 | 响应 model 标签 | SSE begin / completed |
|---|---:|---:|---:|---:|---|---:|
| backup-docs | 477,839 | 10,299 | 488,138 | 84,480 | 16 次 gpt-5.6-sol | 16 / 16 |
| backup-skill | 265,699 | 5,299 | 270,998 | 78,848 | 16 次 gpt-5.6-sol | 17 / 16 |
| calendar-skill | 609,052 | 7,050 | 616,102 | 134,912 | 1 次 gpt-5.6-sol，**25 次 gpt-6-sol** | 26 / 26 |
| calendar-docs | 443,091 | 9,892 | 452,983 | 84,224 | 16 次 gpt-5.6-sol | 16 / 16 |

共有 74 个模型轮、75 个可观察的流式 attempt 开始事件。backup-skill 有 1 次 `transport` 错误并由正式产品路径自动重试，失败 attempt 的 usage 为空；没有人工重跑该条件。其他组没有流式 error 事件。驱动的 `requests.http_attempts` 字段仍为 null，本表依据轨迹中的 `model_stream` begin/completed/error 计数，不冒充独立 HTTP 传输 hook 的结果。

已报告总量为输入 **1,795,681**、输出 **32,540**、总 tokens **1,828,221**；推理 **9,858** 已含于输出，缓存读取 **382,464**。表中指标覆盖全部 74 个成功终态，但失败的 transport attempt 用量未知，不能把它计成零费用或声称已获得全部计费量。缓存不是本实验变量，不用不同自主轨迹的缓存比例推断 Skill 改善缓存。

日历 Skill 组响应标签的变化与普通组不同，无法从这些标签确认底层模型，但它足以限制同模型因果解释。独立 Memory QA、浏览器验收和后续产品整合也在部分时段并发，且请求有网络/服务波动，因此运行秒数只作记录，不用 200.71 对 276.19 秒宣称 Skill 提速。输入和轮数增加同样不能仅归咎于 Skill。

## 真实暴露的执行问题

- backup-docs 误读不存在的根目录 `data-contract.md`；calendar-docs 探测不存在的 `package.json`。两组随后自主继续，错误与成本都保留。
- backup-skill 尝试 `rg --files`，环境没有 rg，收到 exit 127 后改用内置文件工具。技能包没有消除环境能力假设。
- backup-docs 先以 CommonJS 模式检查浏览器 ES module 的 `app.js`，失败后改用 `node --input-type=module --check < app.js` 成功。这是检查方式错误，不是候选浏览器代码必然错误。
- 三组用后台 `http.server` 重定向到 `/tmp`，Shell 返回 exit 0，而输出含 `Permission denied`，工具被标为 success。这个状态只说明 shell 命令退出成功，不证明服务启动成功。后续 curl 本地端口失败；前台 server 又明确遭到 socket `PermissionError`。当前禁网和隔离命令生命周期不支持模型假设的跨调用本地服务方式。
- backup-docs 的 `run_tests --test-reporter=spec` 被 `test_reporter_reserved` 拒绝，随后去掉保留参数成功；工具契约发现不足造成可避免的步骤。

这些错误没有导致最终候选浏览器验收失败，但说明 agent 还缺少明确的可用工具/执行环境说明，尤其是浏览器验证入口。Skill 工作流要求检查真实界面，然而模型环境没有可用的浏览器检查工具；反复尝试后台服务会增加成本。改进应先让环境能力和工具边界可发现，并提供受控的实际 Web 验证路径，再评估 Skill 是否能降低重复错误，不能靠给 Skill 组额外验收答案制造优势。

## 可复核证据与结论

- Runtime：[backup-docs](results/backup-docs.json)、[backup-skill](results/backup-skill.json)、[calendar-skill](results/calendar-skill.json)、[calendar-docs](results/calendar-docs.json)。
- 独立浏览器：[backup-docs](results/backup-docs-browser.json)、[backup-skill](results/backup-skill-browser.json)、[calendar-skill](results/calendar-skill-browser.json)、[calendar-docs](results/calendar-docs-browser.json)。
- 独立身份与机制审计：[skills-audit.json](skills-audit.json)，核对共同 seed、公开规则、配置、源码、评分器、预算，以及首请求技能目录和实际正文/资源调用。
- 配置和初始身份保存在本地 `evidence/<条件>/launch.json`；完整工具、Skill 参数和流式 attempt 事件在本地 `workspaces/<条件>/.aster/runs/<run_id>/trace.jsonl`。候选、截图与下载证据保留，未由操作员修改。

本轮证明 Aster 能在相同现有网页上自主完成这两类维护工作，Skills 也已出现真实加载；**尚未证明 Skill 带来更高成功率或更低成本。** 下次可保留本轮作为开发验收材料，预先选择更需要复用操作流程的新任务、校准服务端一致性，并继续分别记录目录发现、正文加载、资源读取、重复工作与独立交付质量。每类仅两个条件各一次，不能泛化为 Skills 普遍有用或无用；本轮没有为了结果好看再跑。
