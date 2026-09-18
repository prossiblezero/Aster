# uniqwords

`uniqwords.py` 是一个仅使用 Python 标准库的命令行工具。它读取完整的 UTF-8 文本，按照 Python `str.split()` 的空白规则拆分单词，统计每个完整单词的出现次数，并按 Python 字符串字典序输出 JSON。

## 要求

- Python 3.9 或更高版本
- 运行自测需要 pytest

## 用法

```console
python uniqwords.py INPUT.txt
```

必须恰好提供一个输入文件路径。文件名含空格时应由 shell 引号包围：

```console
python uniqwords.py "my input.txt"
```

查看帮助：

```console
python uniqwords.py --help
```

成功时退出码为 0，stderr 为空，stdout 只包含一个 JSON 对象和末尾换行。空文件或全空白文件输出 `{}`。匹配规则如下：

- 区分大小写。
- 标点保留为单词的一部分。
- 不执行 Unicode 归一化。
- 支持 Unicode 空白、LF、CRLF 和无末尾换行。
- JSON 中的中文等非 ASCII 字符直接显示，不转义为 `\uXXXX`。

参数错误、未知选项、文件不存在、目录路径和非法 UTF-8 输入均以退出码 2 结束，stdout 为空，stderr 显示简短错误。

## 示例

仓库中的示例可这样运行：

```console
python uniqwords.py examples/input.txt
```

预期输出见 `examples/output.json`：

```json
{"Hello": 1, "hello": 1, "世界": 2, "你好，世界！": 1}
```

## 自测

在项目根目录运行：

```console
python -m pytest -q
```
