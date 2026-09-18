# mdcheck 项目约定

mdcheck 是一个运行时仅依赖 Python 标准库的 Markdown 本地链接检查 CLI，最低 Python 版本为 3.12。

默认文档目录是 `manual`。CLI 为 `python -m mdcheck [ROOT] --format text|json`；模块实现放在 `mdcheck.py`。JSON 字段固定为 checked 和 broken；错误记录包含 source、line、target、reason。缺失路径 reason 为 missing_target。退出码固定为：无坏链接 0、有坏链接 1、参数或输入错误 2。

source 必须是相对扫描根目录的 POSIX 路径，排序稳定；不得联网或修改被检查的文档。交付包括 README、pyproject.toml 和真实测试，其他人可以安装后使用。
