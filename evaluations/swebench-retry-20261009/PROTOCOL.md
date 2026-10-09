# SWE-bench 五次故障补测

2026-10-09，用户授权对原轮次中五次因主机卡顿或模型连接故障留下空补丁的尝试各补一次。原始实验、补丁和成绩保留在 [2026-10-08 对比](../swebench-compare-20261008/REPORT.md)，不按补测结果追加尝试。

| Agent | 题目 | 原尝试的故障记录 |
|---|---|---|
| Aster | sphinx-doc__sphinx-9658 | 主机卡顿时模型流中断，超时后空补丁 |
| Aster | sympy__sympy-13031 | ConnectError 后 provider_error，空补丁 |
| pi | django__django-15957 | 主机卡顿时模型流中断，超时后空补丁 |
| pi | sympy__sympy-18199 | ReadTimeout / ConnectError，空补丁 |
| Codex | django__django-15382 | 持续 ConnectError，未收到成功模型响应 |

`rerun.py` 直接调用原轮次冻结的 `compare.py`，仅将输出目录和 gateway 的记录目录指向本轮。复用同一 Aster 源码、pi 1.1.0、Codex 0.154.0、官方镜像及官方评分器；每次新建容器，清空会话与记忆。模型仍为 gpt-5.6-luna，reasoning=medium，context=64000，output=8192；每题 900 秒、4 CPU、8 GiB，最多同时运行两题。五次的固定调度顺序见脚本，不使用旧补丁或评分反馈帮助解题。

正式运行前检查主机压力、冻结文件哈希、镜像身份和模型连通性。连通性检查只发一个无关题目的简短请求，记录在本地 preflight，不计作解题尝试。评分使用原始数据行、未修改的官方 SWE-bench 评分器和独立 run ID `retry-<agent>-20261009`。

报告并列给出原始十题成绩、五次补测结果，以及将这五次故障尝试替换后的十题汇总。其余 25 次结果原样沿用；替换口径不取两次中的最高分。补测成本另列，原失败尝试的消耗仍保留。十题小样本、原轮次其余服务波动和历史选题限制仍然存在，不代表完整榜单或稳定排名。
