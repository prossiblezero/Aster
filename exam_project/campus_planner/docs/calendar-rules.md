# 日历ICS互操作

“导出日历”下载UTF-8文件campus-planner.ics，仅导出未完成任务。没有未完成任务也产生有效空VCALENDAR。VCALENDAR包含VERSION:2.0与PRODID，事件为VEVENT。课程和标题组成SUMMARY：课程 + "：" + 标题；notes作为DESCRIPTION。

稳定项目约定：UID为任务id加@campus-planner.local，同一任务反复导出UID不变；DTSTAMP为导出时刻UTC。按time-rules将截止时间从北京时间转换为UTC的DTSTART，DTEND加30分钟，格式YYYYMMDDTHHMMSSZ。

按iCalendar文本转义顺序处理反斜杠、换行、逗号、分号，SUMMARY与DESCRIPTION均转义。文件用CRLF分隔行并以CRLF结束；长行按UTF-8字节折行，每个物理行最多75字节，续行以一个空格起始，不切断UTF-8字符。不能用字符数代替字节数。无需日历账户连接或网络服务。
