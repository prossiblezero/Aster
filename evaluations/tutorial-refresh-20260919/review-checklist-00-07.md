# 00–07 教程独立验收清单

基线：`1c67fbc8b9dce9999cdac317dba20fe8687b7747`。以下来自只读源码核对，用于候选完成后的独立验收；不是已通过记录。源码行号指基线。

## 必须纠正的过时描述

- [ ] **01:69 工具执行顺序**：默认 `read_parallelism=1`；大于 1 时只有纯 `read_file/search/list_files` 批次可并行，预检与结果提交有序，混合批次串行。不能再断言全部串行、并发只有 Worker。依据：`runtime.py:828,870`、`config.py:130`。
- [ ] **02:63 请求源码节选**：实际传入 `effective_output_tokens`，不是可能为 `None` 的 `output_tokens`。依据：`provider.py:428`、`config.py:182`。
- [ ] **02:137 模型切换与恢复**：普通恢复仍检查身份；显式 `allow_model_change` 可转换可移植历史，安全配置须一致，原生推理状态不是无损跨供应商迁移。依据：`session.py:206`、`runtime.py:133,364`。
- [ ] **06:216 策略版本**：当前为 `deterministic-v7`；历史 v4 输出可标注保留，不能声称当前显示 v5。依据：`context.py:30`。
- [ ] **07:20,49,98,108,232 归档范围**：当前 run 加同一会话中显式继承的祖先观察 ID；不能误写成只能当前 run，也不能扩大成任意运行。恢复会校验 session、安全身份及归档完整性。依据：`runtime.py:250`、`session.py:230,271`。

## 影响理解的必要补充

- [ ] **02 流式路径**：`stream=False` 默认，仅 Responses 支持；走 `complete_responses`，delta 是临时预览，完整终态解析后才执行工具。CLI 预览到 stderr、最终答案到 stdout。依据：`config.py:108,137`、`provider.py:446`、`streaming.py:265`、`cli.py:201`。
- [ ] **02/05 诊断与重试**：介绍 `ModelReply.diagnostics`、请求/响应模型、attempt、分类与 usage 是否观测到；空响应最多额外重试一次，总请求尝试不超过 3，不能把未报告 usage 当零。依据：`provider.py:558,610,697`。
- [ ] **04 依赖及进程生命周期**：默认依赖不跨命令保留；复用只保留受控 `.venv/node_modules`、仅本进程有效、要求 `sandbox=required`；失败可能丢弃环境。后台进程不跨调用存活，临时文件使用 `.tmp/TMPDIR`，禁网包含本地 TCP。依据：`config.py:131,135`、`safety/runner.py:51,110,203`。03:70 的正式测试入口和重装提示已有，可引用。
- [ ] **06/07 输出边界**：区分命令输出 4 MiB 捕获上限、8192 字符预览、全请求预算压缩；已捕获命令正文先归档，捕获上限外的 overflow 无法回读。“无固定工具 token 上限”不等于完整命令输出必进下一轮。依据：`safety/runner.py:22,439`、`runtime.py:1163`。
- [ ] **03 工具补充或明确交叉引用**：`patch_file.edits` 同文件批量修改须匹配原始文件的唯一、不重叠片段；模型批次参数错误会阻止整个批次执行。依据：`tools.py:68,455`、`runtime.py:840`。不要求新增章节。

## 实验与交付约束

- [ ] `run_examples.py` 原样执行正文代码块；不屏蔽 tiktoken、不删除断言、不自动跳过失败；04 保持 required，不能以 best_effort 降级换取通过。
- [ ] 如需环境说明：离线实验不调用模型 API，但 tiktoken 首次使用可能下载词表；完全断网前准备缓存。Linux 实验要求支持 Landlock/seccomp；不能把外层嵌套沙箱限制描述成产品能力缺失。
- [ ] 最终 00–07 及全套教程实验由操作员在宿主 `aster` 环境独立复验；Aster 自行完成网站构建与正式静态测试。报告分别归属验证执行者，保留首轮失败和环境纠偏。
- [ ] 修改后的 Markdown、教学网页和源码镜像一致，源码节选和默认值以当前实现为准。

00 现有入门命令、03 核心工具与正式测试说明、05 状态划分，以及根 README/学习 README 的主要描述未发现必须推翻之处；不要求为凑覆盖率重写。
