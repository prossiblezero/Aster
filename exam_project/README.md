# Aster 生成的小项目

这里只保留可以独立运行的产品代码、测试、说明和示例。评测驱动、对照结果与历史运行证据已归入 [evaluations](../evaluations/README.md)；教学网站继续放在 `learn_web/`。

以下七个产品从历史候选原样复制，没有人工修补实现。各目录的 `ORIGIN.md` 记录来源，[迁移记录](../evaluations/archive-migration-20260918.json) 保存逐文件摘要。历史验收不代表新实验结果，也不代表完整的发布审计；使用限制以各项目 README 为准。

新增 [课业清单](campus_planner/README.md)：大学生本地作业管理网页，支持添加、编辑、搜索筛选、截止日期与完成状态、JSON备份和ICS日历导出，刷新后保留数据，适配手机。本轮增加撤销最近一次删除，最终19项Node自测、32项独立浏览器验收通过；[来源与人工恢复记录](campus_planner/ORIGIN.md)保留首次TUI切换失败和修复补验。

新增 [期末复习安排器](study_planner/README.md)：录入课程、考试日期和剩余小时，设置开始日期与每日上限，生成截止日前的复习计划，不足时提示缺少小时；支持编辑删除、刷新保存和手机使用。由Aster从模糊需求经一次澄清实现，首测8/9、唯一返修后9/9，实际应用Node回归2项通过；[来源记录](study_planner/ORIGIN.md)保留框架修复、超时及返修。

新增 [实验数据整理器](lab_data/README.md)：终端交互或命令行读取课程实验CSV，导出原始、汇总、异常三张Excel工作表，保护源文本并拒绝覆盖文件。2026-09-19隔离复测与一次修复版返修后，独立24/24、正式自测5/5及干净安装/交互检查通过；[来源](lab_data/ORIGIN.md)保留首次失败、服务故障、明确缺陷反馈和追加成本。

预览以上任一网页：`conda activate aster`，进入对应目录后执行 `python -m http.server 8000 --bind 127.0.0.1`，浏览器打开 `http://127.0.0.1:8000/`。浏览器验收由操作员执行，不代表Aster自主浏览器测试。旧CSV→Excel候选的缺陷与未通过结论仍保留在[v0.2完整验收](../evaluations/v0.2-release/REPORT.md)，没有覆盖为本次成功。

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
