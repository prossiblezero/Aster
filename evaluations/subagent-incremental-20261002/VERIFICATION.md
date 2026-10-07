# 验证记录

使用 `/home/dzh/.conda/envs/aster/bin/python`（Python 3.12），源码检查显式设置 `PYTHONPATH=src`。

## 已确认的回归边界

- 七个新增案例对本轮前冻结源码：**7 failed**，11.45 秒；并未用更早的 Git HEAD 代替未提交基线。
- 新实现定向检查：**11 passed，58 deselected**，5.79 秒，包含既有等待契约。
- 通过事件控制两个子 Runtime，先完成的结果在另一个子任务仍被阻塞时返回；消费后下一次等待仍阻塞，Final 不能提前完成。
- 已完成的活动/恢复/不可用快照能够及时返回；界面查询不消费，工具查询消费后不会自动重复通知。
- 自动、单个和列表三种模型交付保留源码、结论、过期路径和验证信息。原始工具内容、UI 快照和磁盘内容保持完整；自动截断答案可通过状态工具取回全文。
- SOURCE-HASHES.json 保存本轮前后各 55 个生产文件哈希；本轮前基线与上轮已冻结 candidate 完全一致。

固定消息重放的代码逐项断言：只有 `read_evidence` 哈希表被 `read_paths` 替代，其余字段不变。按六题各两个交付读取全部 12 条历史消息，输出中保存消息哈希和来源路径，没有挑选较有利样本。

## 完整回归与安装

- 完整回归（含教学站点）：**1,105 passed，4 skipped**，147.98 秒。
- wheel 安装目录下运行 Worker、精选交付和工具契约测试：**170 passed**，51.99 秒。
- Ruff 检查 src、tests 及本轮重放脚本通过；`git diff --check` 通过。
- 第 13 章可运行示例通过；教学网站重新生成。
- 先前两轮的冻结生产源码均重新核对哈希，未改变历史版本。

wheel 使用项目声明的隔离构建，安装到 `/tmp/aster-incremental-wheel-20261002`。从 `/tmp` 启动测试，在同一测试进程先断言导入路径指向安装目录；逐项核对 checkout、冻结 candidate、wheel 与安装目录的全部 55 个生产文件，内容一致。

wheel SHA-256：`a72849e1bc6f69457a9025ce73ac65d46276096a875486a74859644bfad9858a`，摘要见 [WHEEL-VERIFICATION.json](WHEEL-VERIFICATION.json)。依赖仍使用同一 Conda 环境，不将独立安装路径等同于跨平台兼容验证。原始日志和 wheel 保存在 artifacts/private/subagent-incremental-20261002。

```bash
PYTHONPATH=src python -m pytest -q
python -m ruff check src tests evaluations/subagent-incremental-20261002/compare_messages.py
python learn_web/build.py
```

## 模型探测

一次 Responses 流式请求仅要求回答 OK，使用项目现有配置及凭据；HTTP 401，无供应商 usage。未输出凭据，未修改 .env，未追加批量请求。它只证明当前服务仍不可用，不对模型能力、正确率或全队成本评分。
