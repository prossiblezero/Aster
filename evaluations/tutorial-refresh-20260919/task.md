请亲自审查并更新Aster教程Markdown和教学网站，使它们准确讲解当前仓库实现。

读者会Python但不了解Coding Agent Harness。保留现有16章的循序讲解、源码走读、图示、可运行实验、预期结果、练习和参考答案，不把教程改成版本更新日志、功能列表或简短摘要。沿源码讲清楚为什么、数据怎样流动、如何实际使用；已经准确的段落不必重写。继续使用现有网站设计、导航、搜索、进度和静态构建器，不重做UI。

主要检查docs/learning/README.md、00到15章及引用的当前使用说明。对照src/aster、tests、README、docs/configuration.md、docs/skills.md和必要的最新实测结论，逐章核验现行入口、默认值、参数、函数、执行顺序和能力限制。特别核对provider/profile与模型切换、Responses流式、工具并行与批量补丁、正式测试和依赖复用、上下文与归档、会话恢复、Memory/Dream、Skills、Worker、评测与后训练接口。这里列的是核查范围，不是要求宣称每项都已经支持或有效。

以当前源码为准。明确区分已实现机制、默认关闭的可选项、任务中真实验证过的行为与尚未实现/未证明的能力。历史评测中的首次失败、人工反馈、返修和未知用量不可改写为一次成功；教程链接权威报告即可，不堆硬编码成绩。SFT/GRPO已有方案/数据契约不等于训练器、权重更新或训练效果。不要把旧计划、旧建站约定中的限制当成当前实现。

可修改：docs/learning/内教程与必要的实验运行入口；当前README.md、docs/configuration.md、docs/skills.md中的相关过时说明；learn_web/的当前站点说明、可复用任务约定及必要构建代码，最后通过python learn_web/build.py生成网页、搜索索引与源码副本。不要手改生成HTML来代替修改Markdown。不得改src/aster、tests、datasets、scripts、exam_project、evaluations或历史实测报告/原始轨迹；不改变Aster核心行为来迁就旧实验，不增加新工具或训练实现。

必须运行：全部标记的教程离线实验、网站构建、正式run_tests的静态站点检查（python -m pytest -q learn_web/tests/test_site.py）。例子失败要修正文中的实际原因，保留验证意义，不能删实验/断言或跳过测试来过关。需要修测试仅限learn_web自身出现可证实的契约变化，不降低原有验收。校对短源码节选是否仍存在，示意伪代码必须明确标注。最终说明修改了哪些教学误解、哪些实验实际通过、仍未验证什么。

环境：Python来自用户指定aster环境；required沙箱，工具网络关闭，构建依赖已安装。仓库是维护任务隔离副本，不含真实凭证和Git元数据。不要运行git status；使用git_diff。文件工具可读src/aster。Shell运行本仓库的Aster实验时用PYTHONPATH=src python docs/learning/run_examples.py all，使子进程导入隔离副本源码；正式run_tests仍用无环境前缀的受支持命令。用.tmp/或TMPDIR放临时文件，临时后台进程不能跨工具调用。无需重新安装Aster或生成虚拟环境；不调用真实模型API作为课程实验，不联网、不推送、不部署。

这是一项文档维护任务，请自主完成检查、必要修改和验证。保持已有教学风格，避免只在章节末尾堆一段“当前版本补充”而让正文仍自相矛盾。
