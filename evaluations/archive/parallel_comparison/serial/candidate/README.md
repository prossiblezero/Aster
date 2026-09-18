# tagmerge

`tagmerge.py` 合并一个目录中各个 JSON 文件内相同条目的标签。它只使用 Python 标准库，不会修改输入文件。

## 输入格式

目标目录直接包含的、扩展名严格为 `.json` 的普通文件会被读取；子目录和其他扩展名会被忽略。每个文件必须是 UTF-8 编码的 JSON 数组：

```json
[
  {
    "id": "oak",
    "tags": ["green", "large"],
    "optional_extra_field": true
  }
]
```

`id` 和各标签都会去除首尾空白，结果不会改变大小写，也不会进行 Unicode 归一化。相同 `id` 的标签在所有输入文件间合并、去重并排序，输出对象的键也按 Python 字符串顺序排序。

## 运行

需要 Python 3.9 或更高版本：

```bash
python tagmerge.py DATA_DIR
```

使用工作区自带 fixture：

```bash
python tagmerge.py fixtures
```

输出：

```json
{"birch": ["white"], "cedar": ["coastal", "evergreen", "tall"], "elm": [], "maple": ["gold", "red"], "oak": ["green", "large", "round", "small"], "银杏": ["秋天", "黄色"]}
```

查看帮助：

```bash
python tagmerge.py --help
```

输入有效时程序退出 0，stdout 仅包含结果 JSON 和换行。参数或输入无效时程序退出 2，stdout 为空，stderr 包含简短错误。

## 测试

```bash
python -m pytest -q
```
