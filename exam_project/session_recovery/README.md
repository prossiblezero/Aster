# 真实会话恢复与较小上下文跟进

冻结源码：`d20c38fface3751fd366e303e77c81d6ce66c6f8`。本实验只在本目录准备协议、独立验收和调度器，不修改产品代码。两阶段各一次机会，失败不追加采样或人工修补候选。

阶段 1 从空目录实现 [uniqwords](requirements.md)。第一次成功文件写入后的下一次 `complete()` 在 HTTP 之前注入一次 `ProviderError`，明确记为模拟故障。保存原 run 后，新建 Runtime，执行真实 `resume()` 和普通 `continue_task("继续剩余工作。")`。故障前后共同使用最多 30 步、16 轮、1,800 秒，恢复预算扣除已经消耗的步数、轮数和时间；模拟故障轮也消耗一轮。

阶段 1 完成并通过独立验收后，在同一个已恢复 Runtime 的空闲边界调用 `switch_model("compact-sol")`。该具名 profile 保持同一个 `gpt-5.6-sol`、Responses 端点与凭证，只把总上下文从 128,000 改为 64,000；输出仍为 32,768。随后把 [跟进要求](followup.md) 作为普通用户反馈继续，阶段 2 另给 30 步、16 轮、1,800 秒。这验证真实跟进与较小窗口切换，不声称跨模型、跨 provider，也不把 profile 切换后的采样算作初始任务重跑。

两阶段均关闭 Memory/Dream、Skills、Worker、流式及只读工具并行，required 沙箱、禁网。冻结版本尚无环境复用配置，执行命令仍采用每次独立副本。Skills 无可用目录，并用只会缩小权限的实验 hook 拒绝 skill_load；没有给模型加载技能正文。

候选代码、README、自测全部由真实 Aster 写入。操作员只提供冻结需求、恢复信号与预先授权的阶段 2 需求；无针对候选缺陷的临时提示，无候选人工修改。HTTP hooks 只记录尝试次数、重试、状态码和耗时，模型与原 transport/retry 参数不变。

独立验收在另一个 required 沙箱中运行：阶段 1 为 8 项；阶段 2 保留这 8 项并增加 4 项 casefold 验收。空目录必须执行全部 8 项且整体失败；阶段 1 候选在阶段 2 开始前接受 12 项负控制，4 项新增测试均须失败。负控制结果不交给 Aster。独立测试代码不进入生成时的候选目录。

驱动在 Python 启动前指定冻结 `src` 和 `scripts` 的 PYTHONPATH，并核对源码、实验文件、实际导入路径、配置与端点；凭证仅在内存比较，输出文件扫描不应包含明文凭证。成功工具 ID、首次写入后的文件摘要、恢复前后的调用配对以及每阶段原始事件均保留。合并 token 统计保留缺失字段，不把模拟故障记成真实 HTTP 失败。

运行方式：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH \
PYTHONPATH=<本目录>/.source/src:<本目录>/.source/scripts \
/home/dzh/.conda/envs/aster/bin/python <本目录>/run.py \
  --source-digest <冻结源码摘要> --env-file /home/dzh/Aster/.env
```

运行目录已存在时拒绝覆盖。`.source/`、原始 `run/evidence/` 及候选 `.aster/` 本地保留并忽略，报告与可公开审计结果单独保存。框架离线检查不得调用真实模型。
