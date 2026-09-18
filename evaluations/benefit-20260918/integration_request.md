请在已有且通过验收的“课业清单 + JSON备份”基础上，完成最终日常可用版本：保留基础管理和备份导入导出，增加ICS日历导出。当前工作区来自已通过验收的备份候选，勿删除或重写已有功能。

根据docs/calendar-rules.md、time-rules.md及data-contract.md实现“导出日历”按钮，下载campus-planner.ics：仅未完成任务、稳定UID、北京时间转UTC、30分钟、跨日、正确转义、CRLF、UTF-8字节折行；空列表也导出合法日历。不更改docs公开规则、不联网、不安装依赖。

补充独立纯逻辑node:test断言，更新README写明基础管理、备份和日历的实际使用步骤及本地存储边界。使用run_tests正式执行全部测试，只有真实失败再修复。此环境的沙箱Shell后台服务不能跨工具调用保留，不必再启动HTTP服务或curl；操作员会在沙箱外通过真实浏览器验收整个产品。浏览器ES module的.js不能直接当Node CommonJS加载；纯逻辑.mjs可用Node内置测试运行器。

不要在run_tests设置自定义reporter，它由工具管理。完成后报告确实执行的测试与启动方式，不把尚未做的浏览器检查写成通过。
