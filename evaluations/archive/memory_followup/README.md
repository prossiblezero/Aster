# 持久 Memory 的自然保存与中文后续任务对照

本次使用全新的 `notetally` 标准库 CLI，源码固定为 `d20c38fface3751fd366e303e77c81d6ce66c6f8`。与先前 mdcheck 实战、流式和缓存探针分别报告，不沿用历史成绩。两条件为持久 Memory 关闭/开启，每条件两个阶段，各阶段仅一次真实 Aster 任务；不因保存数为 0、任务失败或验收失败追加尝试。

两组从相同 [PROJECT.md](seed/PROJECT.md) 与两份中性便签夹具开始，没有实现。长期项目约定明确：默认便签目录为 `inbox_notes/`。阶段 1 的[中文需求](phase1.md)只要求阅读项目约定、完成统计工具及 README/自测，没有要求保存记忆。阶段 2 使用同一候选目录和**全新 Runtime、全新会话**，以[不同中文表述](phase2.md)追问默认目录并增加 `--total`。历史对话不接续，只有 Memory 开启组能自动读取其持久记忆；两组仍可正常阅读现有源码、README 和 PROJECT.md，所以实验不强迫无 Memory 组遗忘已有文件。

固定 `gpt-5.6-sol`、Responses、同网关/凭证、128,000 总上下文、32,768 输出、300 秒请求超时、30 工具步/16 模型轮/1,800 秒每阶段。stream 关闭，工具并行 1，Skills 无目录/无激活，Worker 为 0，Memory evolution 与 Dream 关闭，required 沙箱和禁网。唯一区别为 `memory_enabled`，及该开关正常决定的发现目录、能力提示、保存与召回。其他功能源码、工具、模型、需求和夹具完全一致。先执行 off 阶段 1、2，再执行 on 阶段 1、2；固定顺序和每组一次序列限制性能与因果推断。

操作员只编写需求、初始项目约定/夹具、驱动和独立验收；所有候选代码、README、自测由 Aster 编写或修改，运行后不人工返修。每阶段开始前执行独立负控制，阶段 1 的无实现目录必须整体失败，阶段 2 会记录增加新功能前的验收情况。验收脚本仅在隔离副本运行，不提供给 Aster；基础阶段 7 项、后续阶段 8 项，包含默认目录、显式目录、递归、UTF-8/中文/空白/注释、坏格式及参数错误、不写入输入，以及新增总数功能。两阶段的 PROJECT.md 和夹具必须保持原始哈希。

逐阶段记录完成状态、独立质量、工具/模型轮、HTTP 重试、用量、耗时、工具发现与自然 `memory_store` 次数/成功数、保存摘录和证据来源、后续新 Runtime 首请求中实际出现的旧记忆及全程 recalled IDs。阶段 2 最终答复是否正确说明默认目录单列，不把模型自报当作功能验收。自然保存为 0 也是有效观察，不额外提示保存。来源失效/注入拒绝属于本轮产品离线机制测试，不在这个真实任务中混入新的改动。

驱动复用**冻结源码内**已验证的 stream comparison 计时与只读源码检查 helper，并核对 `aster`、`benchmark_support` 及已加载子模块都从冻结根导入。所有 Python/测试/候选执行均用 `/home/dzh/.conda/envs/aster/bin/python`，启动进程前设置 `PATH=/home/dzh/.conda/envs/aster/bin:$PATH` 与 `PYTHONPATH=<冻结根>/src:<冻结根>/scripts`。源码全部 `.py` 哈希、配置、连接组合指纹、需求/验收器/驱动/seed 哈希在阶段启动前冻结，结束后检查漂移；明文凭证不进入报告。

```text
/home/dzh/.conda/envs/aster/bin/python exam_project/memory_followup/run.py \
  --source-path <冻结根> --describe-source

/home/dzh/.conda/envs/aster/bin/python exam_project/memory_followup/run.py \
  --source-path <冻结根> --source-digest <聚合SHA256> --condition off --phase 1 \
  --env-file /home/dzh/Aster/.env
```

同参数依次改为 off/2、on/1、on/2；拒绝覆盖已有阶段结果或任务证据。每阶段允许产品原有的至多三次 HTTP 尝试规则，记录真实次数，不在驱动中重复任务。四阶段总上限为 64 个模型轮、120 个工具步和 7,200 秒；供应商失败/重试可能消耗预算，未观测用量保留未知。

离线检查：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH PYTHONPATH=<冻结根>/src:<冻结根>/scripts \
  /home/dzh/.conda/envs/aster/bin/python -m pytest -q exam_project/memory_followup/test_driver.py
```

该任务一旦用于工程评测，即不能再标为未见后训练测试任务。
