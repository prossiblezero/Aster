# linecounts

标准库 Python 命令行工具：以 UTF-8 逐行读取文件，对每行调用 `str.strip()`，忽略空行，统计剩余整行文字的出现次数。

## 用法

```sh
python linecounts.py INPUT.txt
python linecounts.py "含 空格.txt"
python linecounts.py --help
```

必须恰好提供一个输入路径。以 `-` 开头的路径可放在 `--` 后面，例如 `python linecounts.py -- -input.txt`。

区分大小写，不拆分单词，不改变行内空白，不做 Unicode 归一化。支持 LF、CRLF 和没有末尾换行的文本；只读取，不修改输入文件。

输出为一个 JSON 对象加换行，键按 Python 字符串字典序排列，值为整数，中文不使用 `\u` 转义。空文件或全空白文件输出 `{}`。

## 运行示例

假设 `示例 文件.txt` 内容如下：

```text
 苹果
banana
苹果

Banana
```

运行：

```sh
python linecounts.py "示例 文件.txt"
```

stdout：

```json
{"Banana": 1, "banana": 1, "苹果": 2}
```

成功退出码为 0，stderr 为空。缺少或多余参数、未知选项、无法读取文件（包括不存在或目录）、无效 UTF-8 均退出 2，stdout 为空，stderr 输出简短错误且没有 traceback。`--help` 显示帮助并退出 0。

## 测试

工具本身不依赖第三方库。测试使用环境中已有的 pytest：

```sh
python -m pytest -q
```

测试通过子进程验证真实命令行行为，包括排序、空白处理、大小写、Unicode、换行格式、带空格的路径、输入文件不变和错误退出。
