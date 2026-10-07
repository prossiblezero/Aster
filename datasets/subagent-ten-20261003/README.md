# 十题子 Agent 调查评测

共 10 道不同任务、80 个行为问题、7 个源码快照。保留 `subagent-light-20260930` 的六题，增加四个固定 Git 提交：

| 新任务 | 两个工作包 | 提交 |
| --- | --- | --- |
| Boltons | 有序多值映射、迭代边界 | `4e5faa3d7e4008d89e0d8bf1ea87b6d9a061a16d` |
| More Itertools | 缓冲/窗口/切分、迭代器 recipes | `1ea82a711c69f590054987b5cb194157f8ce8ac4` |
| Cachetools | 驱逐/过期/容量、缓存键等价性 | `3c082c654c2804b9354e4b62dbd2994f1aac464d` |
| ItsDangerous | 签名/密钥轮换、序列化/不可信载荷 | `672971d66a2ef9f85151e53283113f33d642dabd` |

`instances.json` 保留题目、工作包、源码获取 URL 与 SHA-256；外部评分器执行探针得到答案，运行中的 Agent 只能看到源码及不含答案的 `REQUEST.md`。新任务随源码保存上游许可证；没有复制整个上游仓库或增加运行依赖。

这是只读调查/受限审查评测，不是代码修复或上游 benchmark 官方成绩。r0–r4 由驱动固定首次分工，r5 才开始评估模型自主选择。迭代中使用过的题不应再称为独立保留集。一个新增 Boltons 探针含不支持的 `fillvalue` 参数，按统一异常契约评分。

使用仓库声明的 Python 3.12 环境，先恢复并验证源码缓存：

```bash
python evaluations/subagent-ten-20261003/fetch.py
python evaluations/subagent-ten-20261003/evaluate.py freeze my-round --source src --repeats 2 --strategy adaptive
python evaluations/subagent-ten-20261003/evaluate.py run my-round
python evaluations/subagent-ten-20261003/evaluate.py summarize my-round
python evaluations/subagent-ten-20261003/analyze.py my-round
python evaluations/subagent-ten-20261003/compare.py my-round
```

`freeze` 不调用模型，执行 80 个事实与 90 个评分负例。`run` 使用已有 `.env` 中的模型服务并产生费用；每轮 20 或 40 回合，失败保留，禁止覆盖已有回合。协议、资源上限与停止条件见[评测协议](../../evaluations/subagent-ten-20261003/PROTOCOL.md)。

`parent_child` 将第一个工作包统一交给一个孩子，父方负责第二个包；`two_children` 复现双孩子策略。`adaptive` 使用相同问题和评分，两臂统一移除外部固定分工的禁止句，Solo/team 分别配置 0/1 个自主委派槽，并分别冻结身份。必须实际发生成功子交付才可能称为协作收益。

新轮次的结果绑定完整冻结摘要。恢复执行会核对结果及启动身份；损坏或错轮次结果要求审计，不会静默跳过或覆盖。旧轮次使用该轮 `artifacts/private/subagent-ten-20261003/<round>/original-harness/` 中的完整驱动依赖及冻结源码复现，不能用改过的当前驱动续跑旧轮。
