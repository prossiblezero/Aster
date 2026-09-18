# Skills 独立真实对照协议

两组从没有候选实现的项目开始，由 Aster 完成相同 [eventcount CLI 需求](requirements.md)。唯一差异是 `on` 组预置一个通用 `cli-quality` 技能包，`off` 组没有技能。包只提供通用 Python CLI 质量检查方法及参考资源，没有候选实现、任务专有例子或隐藏测试答案。技能发现与选择由 Aster 自主决定；不强制工具调用，不因未加载技能或资源而补跑。

固定 `gpt-5.6-sol`、Responses、同一网关和凭证、128,000 总上下文、32,768 输出、300 秒请求超时、30 工具步、16 模型轮、1,800 秒/组。两组各一次，顺序执行；`stream=False`、`read_parallelism=1`、Memory/Dream/Worker 关闭、`reuse_environment=False`、required 沙箱、禁网。操作员仅编写需求、通用技能、实验驱动与隐藏验收；不编写或修复候选代码，不给运行中反馈。

使用冻结产品提交，所有 Python 运行在 `/home/dzh/.conda/envs/aster/bin/python`；`PATH` 前置该环境，`PYTHONPATH` 指向冻结根下的 `src` 和 `scripts`。共享计时/源码核验来自 `../stream_comparison/run.py`，它的哈希纳入实验身份。

```text
/home/dzh/.conda/envs/aster/bin/python exam_project/skills_comparison/run.py \
  --source-path <冻结根> --source-commit <提交SHA> --source-digest <聚合SHA256> \
  --condition off --env-file /home/dzh/Aster/.env
```

第二组仅改为 `on`。驱动拒绝覆盖已有组；核对两组模型配置、连接指纹、源码、需求、验收、驱动及技能内容哈希。两组先对没有实现的目录执行全部 7 项隐藏验收，要求整体失败，然后由 Aster 实现，再通过沙箱隔离副本独立验收。验收器位于候选项目外，在生成期间不可见；Aster 不得改写技能包。

记录目录是否出现在首轮模型请求、正文/资源读取尝试与成功、工具失败、请求数、终态用量、耗时和独立验收。除了 `skill_load`，也单独记录 `read_file` 直接读技能正文/资源的尝试与结果，避免把没有调用专用工具等同于没有看到内容。本试点包位于 `.aster/skills`，普通文件工具继续受保护路径限制。正文和资源加载完成只证明机制走通，不等于质量提升。单任务、各一次、模型轨迹不同；即使端到端或用量有差异，也不能归因于缓存、Memory 或工具并行，亦不足以证明 Skills 的普遍收益。

离线检查不调用模型、不生成候选实现：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -m pytest -q exam_project/skills_comparison/test_driver.py
```
