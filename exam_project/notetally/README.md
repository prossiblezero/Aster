# notetally

`notetally.py` 是一个只使用 Python 标准库的命令行工具。它递归读取便签目录中的 `.note` 文件，并按标签统计便签数量。

## 使用

需要 Python 3.10 或更高版本。

```console
python notetally.py [--total] [目录]
```

省略目录时，工具读取当前工作目录下的 `inbox_notes/`：

```console
python notetally.py
```

也可以显式指定目录：

```console
python notetally.py inbox_notes
```

默认模式下，stdout 只有一行 JSON 对象。标签键按 Unicode 字典序排列，并直接显示中文。例如项目自带便签的统计结果为：

```json
{"done":1,"todo":3,"记事":1}
```

使用 `--total` 时，stdout 只输出一行 JSON 整数，表示所有标签的便签总数。它既可以使用默认目录，也可以显式指定目录：

```console
python notetally.py --total
python notetally.py --total inbox_notes
```

项目自带便签的总数输出为：

```json
5
```

## 便签格式

- 只递归读取扩展名严格为 `.note` 的 UTF-8 文件。
- 每个非空行是一条 `标签: 内容` 格式的便签。
- 只按第一个冒号切分，因此内容可以包含冒号。
- 标签和内容会去掉首尾空白，但标签的大小写和内部空白保持不变。
- 标签和内容都不能为空。
- 首个非空白字符为 `#` 的行是注释，会被忽略。
- 空目录以及只有空白或注释的输入在默认模式下输出 `{}`，使用 `--total` 时输出 `0`。

目录或输入无效时，程序以退出码 2 结束，只向 stderr 写入错误信息，不输出部分统计结果或 Python traceback。工具只读取便签，不会修改输入文件。

## 自测

自测只使用 Python 标准库，并在临时目录中创建测试输入：

```console
python -m unittest -v
```
