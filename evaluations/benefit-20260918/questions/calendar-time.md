请根据课业清单项目已有约定，回答日历导出的时区、事件身份和截止时刻。这是只读资料答疑，不修改文件，不实现功能。可以使用已召回的有效项目摘录，也可以读取工作区文档；不要假定操作系统时区就是项目约定。

现有一项未完成作业，其due字符串为2026-10-09T00:10。最后只输出一个JSON对象（不要Markdown代码围栏），键如下：
- timezone：转换日历时间所用的IANA时区名。
- utc_offset：形如+HH:MM的偏移量。
- stored_due：存储及编辑时保留local_string还是替换为utc_string。
- uid_suffix：附加在任务id后的UID后缀。
- repeated_uid：同一任务重复导出时UID应取same或new。
- duration_minutes：单个事件时长，整数分钟。
- completed_tasks：已完成作业取exclude或include。
- dtstart：该作业导出的UTC DTSTART，格式YYYYMMDDTHHMMSSZ。
- dtend：该作业导出的UTC DTEND，格式YYYYMMDDTHHMMSSZ。
- sources：支撑答案的来源数组。每项为工作区相对文档路径，或形如evidence:HASH的已提供证据引用；只列实际使用的来源。
