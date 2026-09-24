# 第二批：由运行契约确定验证责任

这是首批结束后发现具体机制错误而追加的修复验证。首批的 2 次结果原样保留，不能与本批合并后挑选较好结果。

首批 `c2d1cd5`：csv-ledger 通过，access-report 失败。四次启动均未选择新增的 `verification_owner=parent`。access-report 子任务已交付实现，但任务中的“测试”又触发了正式验证门槛：一个子任务遇到无测试文件后耗尽步数，另一个直接检查已通过却仍试图补正式测试并被父任务取消。父任务后来发生 provider error；独立评分还发现 `ceil` 导入丢失，不能把失败全归因于服务。

修正仅把分工从可选参数改成运行契约：父任务在严格模式下已有最终验证要求时，子任务以交付为完成条件；父任务仍必须通过实际测试。不增加预算，不关闭父任务验证，不改变观察、编辑、沙箱与范围要求；没有严格验证要求的父任务仍使用原有的子任务推断规则。

参考了 pi 官方仓库的 **subagent 示例扩展**，提交 [`d5629e20489ccf770ed90b5a33941cb3b7ef24d0`](https://github.com/earendil-works/pi/tree/d5629e20489ccf770ed90b5a33941cb3b7ef24d0/packages/coding-agent/examples/extensions/subagent)：

- `agents/worker.md` 要求交付完成内容、改动路径和备注。
- `index.ts` 的 `runSingleAgent` 使用独立上下文执行任务，收集消息与 usage；`isFailedResult` 按退出/停止状态判错，parallel 分支把最终输出和诊断交还主任务。
- 借鉴的是“子任务交付结果，由主任务继续整合”的边界。Aster 保留自身的严格最终验证，不复制进程框架、Agent 配置体系，也不声称 pi 核心或其他产品都采用同样的验证规则。

本批仍按 csv-ledger、access-report 顺序各运行一次，与首批完全相同的任务、提示、评分器、GPT 配置和预算；失败保留，不重试。结果同时展示 9 月 24 日历史对照、首批以及本批。子任务完成属于交付指标，不能用“解除重复验证门槛后的完成率”冒充独立验证成功率。

```bash
PYTHONPATH=/home/dzh/Aster/src /home/dzh/.conda/envs/aster/bin/python \
  evaluations/worker-handoff-20260925/rerun.py \
  --source <第二批已验证的产品提交> \
  --protocol evaluations/worker-handoff-20260925/REVISION.md \
  --prepared /tmp/aster-worker-memory-preflight-20260922 \
  --output /tmp/aster-worker-handoff-20260925-v2
```

首批 `freeze.json.settings` 沿用旧驱动的默认 `max_workers=0`，实际各题 `launch.json.settings.max_workers=2`；第二批冻结摘要已改为实际配置。模型用量和评分以逐题真实执行记录为准。
