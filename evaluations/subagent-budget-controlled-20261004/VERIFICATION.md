# 受控评测与采用验证

本轮没有读取、复制、输出或修改 `.env`。真实模型由项目正常配置加载；冻结只保存公开配置身份和 endpoint 哈希，不保存凭据。没有新增生产依赖或付费的预检模型请求。

1. `python evaluate.py freeze`：两版分别通过十题真实启动、三个压力和三个充足条件，共 32 个 Runtime 预检场景；fake model 的 complete 一经调用即失败。原评分的 80 个事实和 90 个负对照通过。首次冻结只用于预检，修复 live 请求重复提示后重新冻结，尚无真实请求时完成。
2. Standard 架构专项只读审查覆盖驱动与协议，核对 WorkerManager 取消/恢复、观察层、预算、账本窗口和失败恢复，无剩余具体阻断。驱动 SHA-256 为 `e862b23279813681036b7179b13866728a1a7214cfb5afe8105a829d2e14376f`。
3. `python evaluate.py run`：exit 0，44/44 首次回合按冻结顺序完成。44 个真实孩子；正常与充足条件初始请求有工具，六个 r6 压力请求实际无工具且原因是 `team_token_limit`。一例 SSL 失败保留，未补跑替换。
4. `python analyze.py`：exit 0，核对 44 个结果、88 个运行、54,807 个事件、237 次模型回复、523 个 artifact。重新评分与 oracle、工作区原文件哈希、源码与依赖归档、事件哈希链、检查点与交付最终文本、ledger/checkpoint/result 用量、前缀读取证据和执行顺序一致。239 次逻辑模型请求；已报告用量至少 3,133,414 token，一回合有未知用量，未按零补齐。
5. 隔离目录 `/tmp/aster-budget-r6-o98knuym` 的 55 个模块先核对为冻结 r6，然后运行 `PYTHONPATH=src python -m pytest -q`：**1,163 passed、4 skipped，129.46 秒**，exit 0。日志 `artifacts/private/subagent-budget-controlled-20261004/isolated-tests.log`。旧版失败回归与安装包检查的历史证据见[原验证记录](../subagent-ten-20261003/VERIFICATION.md)。
6. 采用前主区 55 模块逐字节等于 r5；`git apply --check` 通过后应用既有 r6 生产与回归补丁。采用后 55 模块逐字节等于冻结 r6，两个受影响测试文件逐字节等于上述隔离通过版本。结果保存于私有 `adoption.json`，未改评分或运行中源码。

复算命令（使用项目现有 Python 环境）：

```bash
python evaluations/subagent-budget-controlled-20261004/analyze.py
python -m ruff check src tests scripts learn_web/scripts learn_web/tests evaluations/subagent-budget-controlled-20261004
```

`freeze` 创建新实验；已存在冻结或启动目录时会拒绝覆盖。`run` 恢复仅跳过身份一致的完整结果，遇到已启动但缺失结果的目录会停止要求审计。重跑命令不会替换第一次结果。

报告更新时间晚于付费冻结；只读分析器独立记录 SHA-256，不参与模型输入、运行控制或评分变更。生成文档与最终检查结果追加如下。

收尾曾误用 `ruff check .`，扫描到本轮未改的数据集夹具与示例目录，报告 689 项问题。随后依据 README 与 CI 的正式范围检查生产、测试、脚本和网站，并增加本轮两个评测脚本，检查通过；未修改这些无关夹具。`git diff --check` 通过。

最终文档检查：158 个本地链接目标均存在。网站在隔离目录生成 120 个文件，只同步本轮涉及的九个变化文件；生成内容与主区逐字节一致。`python -m pytest -q learn_web/tests/test_site.py` 为 **6 passed**。生产代码仍逐字节等于冻结 r6，两个回归文件仍匹配独立完整测试通过版本。没有提交、推送或发布。
