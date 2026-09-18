# 数据与本地存储

稳定项目约定：localStorage键固定为 campus-planner-v1；数据包版本为1，结构为 {"version":1,"tasks":[]}。

每条任务包含id、title、course、due、notes、completed。id为非空字符串且唯一；title/course去除首尾空白后非空；notes为字符串（允许空）；completed严格为布尔值。due使用 YYYY-MM-DDTHH:mm 形式的真实本地日期时间字符串，例如2026-10-08T23:59。不能将不存在的日期静默修正到另一天。

基础表单新建任务生成稳定唯一ID。编辑保持ID不变。清单只存在当前浏览器站点存储；换端口视为不同站点。不包含真实个人信息，不要求账户同步。
