请根据课业清单项目已有约定，回答浏览器存储与备份规则。这是只读资料答疑，不修改文件，不实现功能。可以使用已召回的有效项目摘录，也可以读取工作区文档；不要把惯例当成项目事实。

最后只输出一个JSON对象（不要Markdown代码围栏），键如下：
- storage_key：localStorage的确切键名。
- version：存储和备份数据包的版本号，整数。
- backup_filename：备份导出的确切文件名。
- merge_by：导入用于匹配任务的字段名。
- same_id：同id现有任务应如何处理，取replace或keep。
- absent_existing：备份中没有出现的现有任务应如何处理，取keep或delete。
- duplicate_import_ids：导入包内部id重复时，取reject_all或last_wins。
- invalid_import：导入校验失败后清单和存储的行为，取no_change或partial_commit。
- sources：支撑答案的来源数组。每项为工作区相对文档路径，或形如evidence:HASH的已提供证据引用；只列实际使用的来源。
