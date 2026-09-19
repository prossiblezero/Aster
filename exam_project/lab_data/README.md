# 实验课程 CSV -> Excel 整理器

这是一个只使用 Python 3.12 和 `openpyxl` 的本地终端工具。它读取 UTF-8（含 BOM）CSV，生成一个恰好包含 `原始`、`汇总`、`异常` 三个工作表的 `.xlsx` 文件。样例 `sample_lab.csv` 是虚构数据，不包含真实个人信息。

## 在干净目录安装

```text
python3.12 -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

依赖只安装到项目虚拟环境，不需要数据库、网站或其他第三方包。

## 命令行运行

```text
python lab_data.py sample_lab.csv --columns temp,ph --output sample_report.xlsx
```

输入 CSV 必须有唯一且非空的第一行表头，并至少有一行数据。`--columns` 指定一个或多个已有的数值列，列名用逗号分隔。CSV 的引号、逗号和中文按标准 CSV 规则读取。

不带参数时会依次询问三个值，行为与命令模式相同：

```text
python lab_data.py
输入 CSV 路径: sample_lab.csv
数值列名（逗号分隔）: temp,ph
输出 Excel 路径: sample_report.xlsx
```

也可以查看帮助：

```text
python lab_data.py --help
```

## 输出约定

- `原始`：原始表头和所有数据单元格按输入顺序写入；文本保持文本，空值为空单元格，公式样式文本不会被执行。
- `汇总`：表头固定为 `column,count,missing,invalid,min,max,mean`。只对所选列统计有限数值；空白是 `missing`，不能解析或为 NaN/无穷是 `invalid`。
- `异常`：表头固定为 `data_row,column,value,reason`，逐个列出所选列的缺失和非法单元格。`data_row` 从第一条数据开始编号，不包括 CSV 表头。

结构错误（如文件不存在、空 CSV、重复或空表头、字段数不一致、列名不存在）会以非零状态退出并显示错误。含有缺失或非法数值的行仍会生成报告。工具不会修改输入文件，不会覆盖已存在的输出，也不允许输入和输出为同一路径；失败时不会留下目标结果文件。

## 运行自测

在已激活的虚拟环境中执行：

```text
python -m unittest -v
```

自测会实际启动 CLI，读取生成的 Excel，并检查三个工作表、统计结果、原始文本类型、交互模式、BOM、CSV 引号、异常数据、结构错误和不覆盖规则。
