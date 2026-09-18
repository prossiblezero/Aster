# Lab 最终独立复核：完整交付门槛未通过

保留原始 `lab-initial-product.json` 的 **16/16 通过、干净环境重建通过**。最终复核发现评分器遗漏和流程证据缺口，因此不将候选提升至 `exam_project/lab_data`，也不追加本版本第二次产品返修。完整证据及源码哈希见 [lab-delivery.json](lab-delivery.json)。

1. **异常表没有保留公式样式非法数值的原文本。** 输入 `measurement\n=1+1\n`，选择 `measurement` 为数值列。程序成功退出，原始表保留了文本，但异常表 `C2` 被序列化为 `<f>1+1</f>`。需求明确要求异常表 value 保留原始文本，因此此项失败。原因是 `lab_data.py` 只在原始表调用 `set_text`，异常表直接 `append(issue.value)`。未来版本应覆盖所有来自 CSV 的文本单元格；本次不修改候选。

   原 16 项评分器把 `=1+1` 放在未选中的 comment 文本列，只覆盖原始表的公式文本保护，漏掉了异常表。补充检查没有修改原评分器或重跑整个实验：[独立补充脚本](lab-supplemental.py)、[失败结果与三表 ZIP/XML 原文](lab-formula-counterexample.json)。使用 aster Python 创建的一次性子虚拟环境，安装相同 requirements（openpyxl 3.1.5、et_xmlfile 2.0.0）；检查后清理，未修改宿主环境或候选文件。

2. **官方资料只有下载证据，没有正文读取证据。** 初始轨迹的 `curl -o .tmp_openpyxl_tutorial.html` 成功下载官方教程，但没有随后读取/输出正文供模型观察。紧接着的 openpyxl import 因 MIME 文件权限失败，末尾 `rm` 还使整个 shell 调用退出码成为 0。不能把“下载过官方 URL”写成“已阅读官方教程并据此实现”。本次按证据缺失记录失败，不补调模型包装首测成功。

其余证据成立：README 返修只修正 missing/invalid 退出语义及 Windows 路径分隔符；四个非 README 文件的内容和权限身份均与 recovery checkpoint 一致；五个最终源文件全部匹配 README 返修 checkpoint。隔离安装后，后续独立的正式测试命令成功导入和使用 openpyxl，两次均 **5 项通过**。原始评测、MIME 环境修复、README 一次返修及本次补充失败分别保留；源码继续位于 `evaluations/v0.2-release/candidates/lab_data`，不宣称一次自主完成或完整验收通过。
