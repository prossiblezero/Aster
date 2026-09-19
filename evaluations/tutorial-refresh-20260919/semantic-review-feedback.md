总任务仍是核对当前代码并维护16章教程和网站。第三轮已补入大量正确内容，但耗尽160工具步，仍缺正式验证且有下面的语义残留。请在当前候选上逐项修好；无需重读所有历史报告或重做准确章节。核心、根tests、数据集和历史证据继续禁止修改。learn_web/source/evaluations下由构建器生成的已引用报告副本属于网站产物，允许正常新增/重建；不要把它们误认成原始evaluations历史证据而删掉，真实历史文件不能改。

请先读本清单涉及的当前文件；修改每个已有文件前 read_file 获得当前版本，尽量用一个 patch_file.edits 提交同文件的完整修改，避免多次补丁造成 fresh_read_required。源码位置仅是导航，请自己核对，不能照抄不正确的伪代码。

1. 02-model-protocols.md:73和143有事实矛盾。provider是身份/配置元数据，实际适配分支由protocol选择（provider.py:425）；默认恢复身份检查与显式allow_model_change要区分，后者可跨协议canonicalize可移植文本/工具历史，不能迁移原生推理状态，也不能更改安全/能力设置。不要写成跨协议一律拒绝。同章127补empty_response至多额外重试一次，总尝试最多3次，非流式_post及Responses streaming都有此边界；unknown usage不是0。
2. 03-tool-system.md已补patch_file.edits，但还需区分同文件补丁原子校验与模型同轮多个工具调用的schema预检：一个调用参数无效，整个调用批次不执行（runtime.py:_execute_batch）。
3. 04-permissions-sandbox.md:91把best_effort与off混为“不能承诺只读/写范围”不准确：off遇只读/写范围直接blocked；best_effort仅无这些约束才允许在隔离不可用时降级，否则拒绝。补一个简短小节说明当前依赖/进程边界（runner.py:51附近、_execution_directory与environment）：默认安装依赖不跨命令，reuse_environment要求required，仅同进程受控.venv/node_modules复用，失败可能丢弃需重装，跨进程恢复不复原该环境；后台进程不跨工具调用存活；临时文件用.tmp/TMPDIR；禁网包含本地TCP。保持04真实required实验。
4. 07-context-archives.md:98、108、232仍限定归档搜索/生命周期只能当前run，与49新增说明矛盾。统一为当前run加显式继承的同session祖先观察ID（不是任意历史run），仍不刷新read_evidence。实验本身只读本run的描述可保留。
5. 08-hooks-and-completion.md完成条件处补run_shell成功不更新正式验证，必须通过run_tests，且正式检查通过不证明教程语义或隐藏产品验收。
6. 09-checkpoint-resume.md已正确区分计数，但92和248旧“模型/协议变更、工作区漂移一律拒绝”仍需改为默认拒绝+显式allow_model_change/adopt_workspace路径。补inflight不确定副作用不能通过adopt_workspace绕过，含worker；受控依赖不跨进程恢复，reuse_environment恢复会使正式验证失效。入口代码若省略转换、校验、归档等步骤应明确是简化示意，不冒充完整原码。请按当前Runtime.resume/session.load_checkpoint核对。
7. 10-layered-memory.md:42和81仍残留“认证不是Memory”“workflow_tool自己搜trace再record”的旧归属，实际workflow_tool委托memory.py模块级store_excerpt认证本run成功read_file证据后record；同步练习答案措辞。明确Settings.memory_enabled默认False，自动召回仅在开启且无已召回IDs等实际条件下执行；保留当前原文摘录/失效限制，不改写成自动经验总结。
8. 11-memory-evolution.md只需补auto_dream=True而Memory或evolution关闭时dream返回skipped，不需改正确实验。
9. 12-plans-and-skills.md:71/74目录仍只讲名称/普通文件，应准确描述单文件与包式SKILL.md及catalog的description/path/base_dir/resources等元信息。77起标为真实代码的SkillSnapshot(body=text, ...)是非法Python语法，skills.load也漏self和脱敏链路；请用真实关键节选或明确标为简化示意的有效Python，不能把脱敏写丢。正文resource不激活正文需明说。新增固定快照/refresh实验已好，保留断言和输出同步。
10. 15-rl-interface.md给training.py添加可点击源码链接，并区分AsterEnv教学奖励与GRPORollout训练合同：cancelled、infra_error、grader_error要求reward=None，不能作为负例直接训练；按实际枚举核对名称。不称已实现训练器。prepare_sft记录的是省略推理的数量，不是推理内容；末尾“数据组织留到以后”也要与已有数据合同对应。
11. learn_web/build.py:242自动插入“当前CLI/TUI每次新任务，checkpoint不等于持续聊天”的course-note已经过时，即使MD正确也污染网页。应删除重复旧注释或更新为与13一致的准确说明，重新生成网站。第04注释如准确则不改。
12. 最后确认05有ModelReply.diagnostics/请求attempt及未知usage的交叉引用，13有Worker持久Memory召回关闭而非仅写入关闭，以及/model [alias]、/new、/continue [--adopt-workspace]、/resume RUN_ID [--allow-model-change] [--adopt-workspace]实际命令用法；漏项只补必要一句。同步根README、学习README、website-task当前语义即可，历史website-run-report不动。

运行：单独run_shell执行python learn_web/build.py，再run_tests执行python -m pytest -q learn_web/tests/test_site.py。不要用复合命令混淆正式验证。图的节点数是旧快照固定值；若真实流程改变，允许在learn_web/tests/test_site.py同步明确计数，同时保留全文节点/边/顺序等价比较，不能删除断言或为凑旧数字扭曲流程。全部课程实验和浏览器由操作员独立验收，不伪称自己跑过。

交付前逐项回看正文，不只追加新段落而留下矛盾句子；禁止屏蔽tiktoken或降低required。此次是有明确人工语义反馈后的维护返修，不是首次自主完成或无干预评测成功。
