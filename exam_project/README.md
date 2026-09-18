# Aster 生成的小项目

这里只保留可以独立运行的产品代码、测试、说明和示例。评测驱动、对照结果与历史运行证据已归入 [evaluations](../evaluations/README.md)；教学网站继续放在 `learn_web/`。

以下七个产品从历史候选原样复制，没有人工修补实现。各目录的 `ORIGIN.md` 记录来源，[迁移记录](../evaluations/archive-migration-20260918.json) 保存逐文件摘要。历史验收不代表新实验结果，也不代表完整的发布审计；使用限制以各项目 README 为准。

新增 [课业清单](campus_planner/README.md)：大学生本地作业管理网页，支持添加、编辑、搜索筛选、截止日期与完成状态，刷新后保留数据，适配手机。运行 `conda activate aster`，进入 `exam_project/campus_planner` 后执行 `python -m http.server 8000 --bind 127.0.0.1`，浏览器打开 `http://127.0.0.1:8000/`。其基础版通过6项Node自测及12项独立浏览器验收；[来源与人工恢复记录](campus_planner/ORIGIN.md)保留完整边界。

先启用指定环境，再进入对应产品目录：

```bash
conda activate aster
cd exam_project/notetally
python notetally.py
python notetally.py --total
python -m unittest -v
```

| 产品 | 功能 | 在对应产品目录执行 | 自测 |
|---|---|---|---|
| [mdcheck](mdcheck/README.md) | 检查 Markdown 本地链接与标题锚点 | `python -m mdcheck examples/docs --format json` | `python -m unittest discover -s tests -v` |
| [csvtotals](csvtotals/README.md) | 按类别精确汇总 CSV 金额 | `python csvtotals.py INPUT.csv` | `python -m pytest -q -c /dev/null test_csvtotals.py` |
| [linecounts](linecounts/README.md) | 统计非空整行出现次数 | `python linecounts.py INPUT.txt` | `python -m pytest -q -c /dev/null test_linecounts.py` |
| [tagmerge](tagmerge/README.md) | 合并 JSON 文件中的标签 | `python tagmerge.py fixtures` | `python -m pytest -q -c /dev/null tests` |
| [notetally](notetally/README.md) | 统计便签标签与总数 | `python notetally.py --total` | `python -m unittest -v` |
| [eventcount](eventcount/README.md) | 汇总 JSON Lines 事件计数 | `python eventcount.py INPUT.jsonl` | `python -m unittest -v` |
| [uniqwords](uniqwords/README.md) | 统计按空白切分的完整单词 | `python uniqwords.py examples/input.txt` | `python -m pytest -q -c /dev/null tests` |

`INPUT.csv`、`INPUT.txt`、`INPUT.jsonl` 换成自己的输入文件；对应 README 有格式和示例。所有工具运行时仅使用 Python 标准库；部分自测使用 aster 环境中已有的 pytest。`-c /dev/null` 避免独立小项目继承 Aster 主仓库的 pytest 配置。

后续实际交付的小项目继续各占本目录的一个子目录；实验需求、驱动、评分器、对照条件和运行报告放在 `evaluations/`，不要再混入产品目录。
