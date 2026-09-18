# notetally

`notetally.py` 是一个仅使用 Python 标准库的便签标签统计命令行工具。它递归读取目录中的 `.note` 文件，并输出每个标签对应的便签数量。

## 用法

```console
python notetally.py [--total] [目录]
```

省略目录时，程序读取命令执行时当前工作目录下的 `inbox_notes/`：

```console
python notetally.py
python notetally.py path/to/notes
```

加上 `--total` 时，只输出所有标签的便签总数。该参数同时支持省略目录和显式指定目录，也可以放在目录之前或之后：

```console
python notetally.py --total
python notetally.py --total path/to/notes
python notetally.py path/to/notes --total
```

输入文件必须使用 UTF-8 编码。每个非空、非注释行是一条便签：

```text
todo: Review the proposal
记事: 中文内容
address: host:port
```

程序只按第一个冒号切分。标签和内容会去掉首尾空白；标签大小写和内部空白保持不变。标签及内容均不能为空。首个非空白字符为 `#` 的行是注释，空白行也会被忽略。其他扩展名不会被读取。

成功时，stdout 包含一行 JSON，键按 Unicode 字典序排列，stderr 为空。例如：

```json
{"done": 1, "todo": 3, "记事": 1}
```

不加 `--total` 时，空目录或仅包含空白和注释的输入输出 `{}`。使用 `--total` 时，stdout 只包含一行 JSON 整数，例如 `5`；空输入输出 `0`。参数错误、目录错误、无效 UTF-8 或便签格式错误会以退出码 2 结束，错误写入 stderr，stdout 不输出 JSON，也不会显示 Python traceback。程序不会修改便签文件。

## 自测

无需安装第三方依赖。运行：

```console
python test_notetally.py
```

自测通过临时目录调用真实命令行程序，覆盖默认和显式目录、`--total` 的两种目录用法、递归统计、Unicode、注释和空输入、原有按标签对象输出、错误参数、无效目录、无效 UTF-8、坏格式，以及输入文件保持不变。
