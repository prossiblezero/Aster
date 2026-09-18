# csvtotals

从空目录实现一个可实际使用的 Python 命令行工具 `csvtotals.py`，只用标准库。

调用 `python csvtotals.py INPUT.csv`，输入 UTF-8（兼容 BOM），必须有 category、amount 两列，可有其他列。忽略完全空白的记录；category 去掉首尾空白且不能为空。amount 是有限十进制金额，用 Decimal 精确求和，拒绝 NaN/Infinity/非法数值。按 category 字典序输出 JSON 对象，每个值是固定两位小数字符串，舍入规则 ROUND_HALF_UP；只在最终分组总和时舍入。支持负数和带引号的逗号分类名。

成功退出 0，只向 stdout 输出 JSON。输入缺列、坏金额、空分类或文件不存在时退出 2，stderr 给简短错误，stdout 为空。不得修改输入。只有表头的文件输出空对象。提供 README、运行示例和 pytest 测试，实际运行测试验证。

不需要发布或 Git 操作。完成后说明用法与验证结果。
