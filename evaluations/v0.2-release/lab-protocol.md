# 实验数据整理器：冻结的产品需求与独立验收协议

本场景验证 Aster 能通过公开 CLI 从空目录交付一个可安装、可测试、可按 README 使用的小项目。它是工程发布验收，不是模型排名，也不是 Memory/Skills 收益比较。

## 给 Aster 的完整需求

实现一个面向实验课程的本地 CSV→Excel 整理器，Python 3.12，实际使用 openpyxl 写 Excel。只需终端界面，不需要网站、数据库或其他第三方依赖。项目交付 `lab_data.py`、`requirements.txt`、README、虚构 CSV 样例及有意义的自测。

公开命令约定：

```text
python lab_data.py 输入.csv --columns temp,ph --output 输出.xlsx
python lab_data.py
```

第二种无参数方式依次询问输入 CSV 路径、逗号分隔的数值列名、输出 Excel 路径；中文或英文提示均可。命令方式和交互方式行为一致。`--help` 能说明使用方式。

读取 UTF-8 CSV，可带 BOM，按标准 CSV 处理引号、逗号和中文。第一行是唯一且非空的列名，随后至少一行数据，每行字段数一致。用户指定一个或多个存在的数值列。处理时不修改输入文件，不覆盖任何已有输出文件，也不允许把输入路径当作输出。失败应以非零退出码及可理解的错误信息结束，不留下貌似成功的结果。没有文件、空文件、错误列名、重复/空表头及字段数不一致属于结构错误。

对所选数值列逐项处理：去除两端空白后为空是 `missing`；不能解析为数值或为 NaN、正负无穷是 `invalid`；只有有限数值参与统计。数据单元格有缺失或非法数值时，仍应生成报告，不能静默丢掉这些行。不要把文本列作为数值处理。

输出恰好三个工作表，首行表头、不合并表头：

| 工作表 | 内容与表头契约 |
|---|---|
| 原始 | 按输入顺序保留原 CSV 表头和所有数据单元格的文本；空单元格可表示为空 Excel 单元格，不能把 `001`、带空白文本或公式样式文本改写为数值/公式。 |
| 汇总 | 每个选定数值列一行，表头为 `column,count,missing,invalid,min,max,mean`。count 为有效数值数目；没有有效值时 min/max/mean 为空。统计可存为 Excel 数值或等价数值文本。 |
| 异常 | 每个缺失或非法数值单元格一行，表头为 `data_row,column,value,reason`；data_row 是不含表头的 1 起始数据行号；value 保留原始文本（空值可为空单元格）；reason 为 `missing` 或 `invalid`。没有异常时仍有表头。 |

汇总和异常工作表的列顺序、数据行顺序不限；原始工作表保留输入顺序。浮点统计允许正常数值误差，不要求特定小数显示格式。表名和列名是事先公开的数据接口，不按 UI 文案或私有函数名评分。

README 应包含在干净目录建立项目虚拟环境、安装 requirements.txt、运行样例、交互使用、运行自测、异常与不覆盖规则的步骤。样例不得包含真实个人数据。允许并要求读取 openpyxl 官方用法文档（https://openpyxl.readthedocs.io/en/stable/tutorial.html），通过获准的 shell 安装依赖，并在后续独立命令中实际使用。依赖安装限项目环境，不安装到宿主 base。

## 执行边界与证据

- Aster 自行编写实现、测试、README；操作员不代写产品代码。
- 总预算 80 工具步、45 模型轮、25 分钟，由公开 CLI 参数和外部墙钟累计控制。恢复消耗剩余预算。
- 产品缺陷最多一次定向返修；相同服务故障最多一次人工恢复；首次结果、返修结果和服务失败分别保留。
- 模型 profile、Aster Git SHA、需求和评分器 SHA256 在启动前冻结。候选目录位于本目录的运行证据目录下；可运行交付后才复制至 `exam_project/`。
- 不把此任务或调试过的评分器当作未见测试集，不据单个样本宣称泛化能力或统计收益。

## 自动产品验收

`lab-grader.py` 使用独立构造的 CSV、公开 CLI 和标准库 ZIP/XML 读取器检查输出；不导入候选函数、不调用候选自写测试充当独立奖励。测试包含正常数据、中文与引号、BOM、前导零文本、缺失、空白、非法文本、NaN/无穷、负数、科学计数法、全无有效值列、无异常数据、交互输入及失败不覆盖。评分器结果是逐项布尔值，任何必需项失败均不能宣布产品通过。

```bash
# 检查现有候选；该命令自身不安装依赖
/home/dzh/.conda/envs/aster/bin/python evaluations/v0.2-release/lab-grader.py \
  /absolute/path/to/candidate --python /absolute/path/to/candidate/.venv/bin/python

# 发布验收必须另做一次：临时目录复制源文件、新建隔离 venv、按 requirements 安装再验收
/home/dzh/.conda/envs/aster/bin/python evaluations/v0.2-release/lab-grader.py \
  /absolute/path/to/candidate --rebuild

# 评分器自己的正负检查，不请求模型、不生成候选产品
/home/dzh/.conda/envs/aster/bin/python -m pytest -q evaluations/v0.2-release/test_lab_grader.py
```

`--rebuild` 从运行评分器的 aster Python 建立一次性子虚拟环境；仅向子环境 pip 安装项目声明，支持正常联网下载，不触及 base。它验证标准安装步骤可复现；操作员仍须对照 README 确认其命令正确，并记录 Aster 自测结果。

以下轨迹门槛单独记录，自动产品通过不能替代：真实公开入口启动；官方文档阅读；获准依赖安装；安装后跨命令 import/实际生成；Aster 自测；README 步骤读回与干净重建。模型身份/usage 异常与预算、失败原因独立报告。发现评分器错误时保留原版及原结果，说明更正并生成新版本结果，不覆盖首测失败。
