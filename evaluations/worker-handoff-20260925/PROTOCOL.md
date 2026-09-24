# Worker 交付分工修复复测（2026-09-25）

目的：检查明确子任务输入、交付边界与测试责任后，是否减少无效检查和协调开销，同时保留最终任务质量。不是新题泛化评测，也不以两次运行证明统计上的可靠性提升。

## 冻结设计

- 复用 `worker-memory-20260922` 已冻结的 `cases.py`、`run.py`、独立评分器及 directed 用户提示，不修改旧实验。
- 仅执行 csv-ledger、access-report 两道 directed 题，各一次，按此顺序。不执行新 solo 轮次，不选择性重试；失败保留。
- 比较 2026-09-24 等待修复版的两次 directed 历史结果。两题已参与故障诊断，只是回归样例；历史对照存在模型输出和服务延迟波动。
- 使用 `.env` 的原 GPT 配置与 Conda `aster`，Responses 流式、64k 上下文、8192 输出上限、请求超时 120 秒、严格完成验证、必要沙箱、无网络。
- 每题全树上限仍为 600 秒、60 个工具动作、40 次逻辑模型调用、500,000 个已报告 token；每个子任务仍为 12 个工具动作、12 轮。不放宽预算或权限。
- 从提交后的源码运行，驱动检查 `aster.__file__`、解释器和源码 diff；冻结提交、源码/驱动/评分器哈希与实际配置。原始证据只在本地忽略目录归档。

## 指标与判读

1. 最终交付：父任务完成且独立评分通过，报告分子/分母。
2. 子任务交付：完成/启动数、停止原因和验证记录；`verification_owner=parent` 的未测试交付不叫“子任务验证通过”。
3. 空转：`approval_unavailable`、`no_tests_collected` 次数；全部调用保留，不能删去失败成本。
4. 协调：worker 工具数、status 调用数；记录是否实际使用测试责任字段。
5. 资源：全树/父子各自模型调用、token、墙钟时间；测试次数和读文件次数辅助解释。token 是消耗代理指标，不等同于账单；记录缓存、缺失 usage 和请求故障，不推算未提供的价格。

只跑这两次即报告结果，不为了得到正收益反复更换提示、评分器或扩充矩阵。若仍慢或更贵，保留负面结论并说明原因。

## 复现

```bash
PYTHONPATH=/home/dzh/Aster/src /home/dzh/.conda/envs/aster/bin/python \
  evaluations/worker-handoff-20260925/rerun.py \
  --source <已验证的产品提交> \
  --prepared /tmp/aster-worker-memory-preflight-20260922 \
  --output /tmp/aster-worker-handoff-20260925
```

`--prepared` 需包含原协议已核验的独立 `graders/`；它们不放进候选任务工作区。真实模型会消耗 API 用量。原始输出的 `freeze.json` 与各题 `launch.json` 记录精确执行身份。
