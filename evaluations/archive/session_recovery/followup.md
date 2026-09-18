请在现有 uniqwords 上继续修改：增加可选的 `--fold` 参数，支持 `python uniqwords.py --fold INPUT.txt` 和 `python uniqwords.py INPUT.txt --fold`。开启后对每个已经按原规则拆分的单词调用 Python `str.casefold()`，然后统计并按结果键排序。默认行为必须保持区分大小写；不要用 lower() 替代 casefold，不做 Unicode 归一化，保留原有输入、JSON、错误退出和不修改文件的约定。

更新 README 的说明、示例和自测，实际重新运行测试后交付。不要联网、安装依赖、发布或执行 Git 操作，不使用 Memory、Skills 或 Worker。

