# uniqwords

从空目录实现仅用标准库的 Python 命令行工具 `uniqwords.py`，调用方式是 `python uniqwords.py INPUT.txt`，必须恰好提供一个输入文件路径。

使用 UTF-8 解码整个文本，用 Python `str.split()` 的空白规则拆分单词，统计每个完整单词出现次数。区分大小写；标点属于单词，不去除标点，不做 Unicode 归一化。按 Python 字符串字典序排列键，stdout 只输出一个 JSON 对象及末尾换行，值为整数；中文直接显示，不输出 Unicode 转义。空文件或全空白文件输出 `{}`。支持 Unicode 空白、LF、CRLF、无末尾换行和含空格的文件名，不得修改输入文件。

成功退出 0，stderr 为空。缺少或多余参数、未知选项、文件不存在、路径是目录、非法 UTF-8 输入，均退出 2；stdout 为空，stderr 给出简短错误，不输出 traceback。`--help` 可以显示帮助并退出 0。

交付 README、示例和 pytest 自测，必须实际运行自测。不要联网、安装依赖、发布或执行 Git 操作，不使用 Memory、Skills 或 Worker。完成后说明用法与实际验证结果。

