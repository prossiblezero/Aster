# mdcheck 功能与验收约定

这是一个 Python 3.12+、运行时仅标准库的本地 Markdown 链接检查工具。实现文件为 `mdcheck.py`，入口为 `python -m mdcheck`；应提供 `pyproject.toml`、可执行的自身测试、README 和示例，允许别人安装和使用。

## 所有阶段通用

- CLI：`python -m mdcheck [ROOT] --format text|json`。ROOT 可为 Markdown 文件或目录；不传时使用 PROJECT.md 中的默认目录，格式默认 text。
- 递归扫描 `.md` 文件，遍历和错误列表顺序确定。不扫描符号链接文件/目录。
- 处理常见行内链接 `[label](target)` 和图片 `![label](target)`；支持 UTF-8 路径和 `%20` 等 URL 百分号编码；相对目标以链接所在文件的目录解析。
- 忽略围栏代码块中的链接。支持以三个或更多反引号或波浪号组成的独占行围栏，闭合符号应与开头对应；无须实现完整 CommonMark。
- 忽略带 URL scheme 的外部链接（如 http、https、mailto）和 `//host/path`。不访问网络。
- JSON stdout 只有一个对象：`{"checked": N, "broken": [...]}`。每条 broken 包含 `source`、`line`、`target`、`reason`；source 是相对于 ROOT 目录的 POSIX 路径（ROOT 为文件则相对于其父目录），line 从 1 起，target 是 Markdown 中原始目标。
- checked 表示实际检查的本地链接出现次数。外部链接、代码块和各阶段明确跳过的片段不计入。存在文件/目录都属于有效的本地路径目标。
- 普通缺失路径的 reason 为 `missing_target`。错误按 source、line、target 排序。同一链接出现多次应按出现次数检查。
- 退出码：无坏链接 0，有坏链接 1，输入不存在或参数错误 2。操作错误使用 stderr；JSON 成功输出不得混入日志。
- 不改写被检查文档；README 说明支持范围、安装方式、用法、退出码及如何测试。

## 阶段 1

默认目录是 `docs`。目标有路径和 fragment 时只检查路径是否存在，不校验 fragment；纯 `#fragment` 链接跳过且不计入 checked。

## 阶段 2

增加 Markdown 标题锚点检查。对纯片段以及指向 `.md` 的本地链接进行 fragment 校验；空 fragment 跳过锚点检查。非 Markdown 文件的 fragment 仍只检查路径。

标题范围为围栏外的 ATX 标题：行首 1–6 个 `#` 后跟空格。slug 规则：标题转小写，删除 ASCII 标点（保留连字符与下划线），空白序列替换为 `-`，保留非 ASCII 字符。重复 slug 后缀依次为 `-1`、`-2`。fragment 做百分号解码后精确匹配；缺失锚点 reason 为 `missing_anchor`。纯片段 now 计入 checked。

