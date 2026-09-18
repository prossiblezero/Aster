# 正式流式对照协议

两组真实任务已在冻结 `15269d62dae4286a7857983b69238b678165b8e1` 上各运行一次，独立验收均为 7/7，无人工反馈或候选修复；见[实测报告](report.md)。流式在有正文的轮次中提前显示内容，但本次任务总耗时更长，不能据单对样本宣称提速或节约。此前冻结 `c02a587` 仅用于离线准备，没有调用真实模型。

两组分别从空目录完成同一 [linecounts 需求](requirements.md)。每组仅一次任务，固定 `gpt-5.6-sol`、Responses、同一 `.env` 网关与凭证、128,000 总上下文、32,768 输出、300 秒请求超时、最多 30 工具动作/16 轮/1,800 秒；仅 `stream` 不同。required 沙箱、禁网，Memory/Dream/Worker 关闭。候选代码、README、自测全部由 Aster 编写；操作员只写协议、调度器和独立验收器，无人工反馈或候选修复。两次独立采样不承诺模型生成路径相同，单对任务不能支持普遍提速结论。

以已提交版本解包得到冻结仓库根目录，操作员在启动 Python **之前**设置 `PYTHONPATH=<冻结根>/src:<冻结根>/scripts`，并前置 aster 环境的 bin。驱动不会修改 `sys.path`。先用指定 Python 运行 `run.py --source-path <冻结根> --describe-source`，保存全部 `.py` SHA256 及聚合 SHA256；再各运行一次：

```text
/home/dzh/.conda/envs/aster/bin/python exam_project/stream_comparison/run.py \
  --source-path <冻结根> --source-commit <提交SHA> --source-digest <聚合SHA256> \
  --condition nonstream --env-file /home/dzh/Aster/.env
```

第二组只把 `--condition` 改为 `stream`。驱动拒绝覆盖已有组目录，核对实际导入的 Aster、benchmark helper 及已加载子模块；每组启动前和结束后比较完整源码清单，并记录及核对驱动、需求和验收器的哈希。第二组还检查配置、模型地址指纹、源码与实验文件仅有已允许的流式差异。候选在 `<组>/candidate/`，结果在 `<组>/result.json`，启动身份/运行结果/空目录负对照在被忽略的 `<组>/evidence/`。

每组在调用模型前必须让独立验收对空目录运行全部 7 项并失败，之后评分同一候选。`benchmark_support.DATASETS` 显式指向本协议目录，评分器在独立 required 沙箱内运行。验收器异常会作为 `grader_error` 保存；模型失败、超时或取消也先保存 Runtime 状态、用量和逐请求计时。验收器覆盖参数、空白/空文件、计数/排序/行内空白、中文重复、CRLF/末尾换行、文件和解码错误及不修改输入。

HTTP 只读 hooks 记录每次真实尝试开始、响应头到达、状态码和重试数，不修改 transport、请求参数或重试规则。逐模型请求记录总时长、终态用量以及首事件/首有效文本：流式首事件来自产品的首个已解析 SSE 事件，非流式只能在完整结果可见时记录，响应头时间另列；没有文本的工具轮 `first_text_seconds` 为 null。重试的每次流终态计时独立保存。首事件口径不同不能直接称作首字收益；首文字、总时长、完整交付、独立验收与真实用量分开报告。缺失用量保留未知，缓存、并行、Memory 和 Skills 不在本对照中归因。

离线健全性检查（不调用模型）：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -m pytest -q exam_project/stream_comparison/test_driver.py
```
