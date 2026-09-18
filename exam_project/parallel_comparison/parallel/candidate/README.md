# tagmerge

`tagmerge.py` 是一个仅使用 Python 标准库的命令行工具。它读取指定目录直接包含的 `.json` 文件，校验其中的记录，并按条目 `id` 合并、去重和排序标签。

## 运行

需要 Python 3.9 或更高版本：

```console
python tagmerge.py DATA_DIR
```

必须恰好提供一个目录。工具不会递归读取子目录，也会忽略非 `.json` 扩展名文件。查看命令帮助：

```console
python tagmerge.py --help
```

成功时，stdout 只包含一行 JSON，退出码为 0。参数或输入错误时，stdout 为空，stderr 包含简短错误，退出码为 2。

## 输入格式

每个 JSON 文件必须采用 UTF-8 编码，顶层为数组。每条记录必须是对象并包含：

- `id`：字符串，去除首尾空白后不能为空。
- `tags`：字符串数组；每个标签去除首尾空白后不能为空，数组本身可以为空。

额外字段会被忽略。工具不会改变大小写，也不会执行 Unicode 归一化。

## 示例

使用仓库自带的六个 fixture：

```console
$ python tagmerge.py fixtures
{"birch": ["white"], "cedar": ["coastal", "evergreen", "tall"], "elm": [], "maple": ["gold", "red"], "oak": ["green", "large", "round", "small"], "银杏": ["秋天", "黄色"]}
```

`oak`、`cedar`、`maple` 和 `银杏` 分别来自多个文件；合并结果会去除重复标签，并按 Python 字符串字典序排列对象键及标签。

## 测试

```console
python -m pytest -q
```
