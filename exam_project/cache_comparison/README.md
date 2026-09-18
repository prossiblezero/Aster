# 稳定前缀缓存机制对照

这是受控请求探针，不是候选项目开发。旧条件固定为 `59ca274f69665100dc20e3ef7343d009a84e3c35`，新条件须等稳定前缀及工具目录修改提交后再冻结。准备阶段和离线测试不调用模型；真实调用须收到该冻结版本后进行。

两组均用冻结源码的 `Runtime.prepare_request` 生成相同初始任务、空历史和同权限的工具输入。96 条中性固定数据使任务超过 1,024 个本地代理 token；末尾只要求 `CACHE_PROBE_OK`。三轮只将 `state.turn` 设为 0、1、2，因此 `remaining_turns` 依次为 3、2、1。输出永不回填历史，不执行返回的任何工具调用。模式从 system 移入尾部 workflow、任务/历史/动态上下文排序和 `observation_read` 可发现目录的产品差异是唯一区别；驱动对其他消息内容、配置、任务、工具 schema 做等价检查。

固定 `gpt-5.6-sol`、Responses、同一网关/凭证、128,000 总上下文、32,768 输出、300 秒/请求、1,800 秒/整个实验。顺序固定旧组 3 次，再新组 3 次，共最多 6 次 HTTP 请求。Memory/evolution/Dream/Worker/stream 关闭，`read_parallelism=1`；Skills 目录为空且从不激活；required 沙箱、禁工具联网、只读、无批准。探针不执行工具，模型传输本身需要访问已配置网关。

每组第 1 次是该条件内的基线，第 2、3 次观察同一长前缀是否被读取。首请求不保证服务端冷缓存，可能已有共享工具或系统缓存。固定先后顺序、每条件仅一个三请求序列、未知路由/缓存写入延迟/失效时间都限制归因；本实验不能得出统计显著性、端到端项目加速、成本下降或任务成功率提升。短标记校验只评价响应格式。若后两次无缓存，也不能仅据此断言供应商完全不缓存。

驱动直接发送预生成的准确 UTF-8 JSON，明确绕开 `ModelClient` 内部三次尝试循环；通过冻结的 `ModelClient` 解析终态与真实用量。每个输入只允许一个 POST，禁止重跑或覆盖已有 `result.json`，失败也消耗该输入的尝试；不为补样本追加请求。请求前写入 `started` 记录，进程异常终止后不会无记录地重试。失败/缺失缓存字段保留未知，真实 0 单独保留。没有设置额外缓存键、温度或 reasoning 参数，两组使用相同服务端默认值并承认该默认值不可控。

驱动使用 `git archive` 核对冻结 `src/aster/**/*.py` 与指定完整 SHA 完全一致，并检查实际 Python 导入路径。运行前后记录源码聚合哈希；启动两组前均完成 prepare，保存 payload 哈希、代理 token 公共前缀及相同内容校验。配置仅保存不含凭证的身份；连接保存 URL+凭证的组合 SHA256，不记录明文凭证。运行顺序、两份 manifest 哈希和统一墙钟起点保存在 `experiment.json`。

用指定 Python，在启动进程之前设置 `PYTHONPATH=<冻结根>/src` 和 aster 环境 PATH，然后为 old/new 各准备一次：

```text
/home/dzh/.conda/envs/aster/bin/python exam_project/cache_comparison/run.py prepare \
  --condition old --source-path <旧冻结根> --source-commit 59ca274f69665100dc20e3ef7343d009a84e3c35 \
  --env-file /home/dzh/Aster/.env
```

新组改为 `--condition new` 及新根/新 SHA。两组准备完毕并核验后，以同参数将 `prepare` 改为 `run`，先 old 后 new。生成的 manifest、三份 payload、result 和最终报告留在本目录；工作目录只用于 Runtime 请求归档，不包含候选实现。`prefix` 指标只用本地 `cl100k_base` 对序列化请求计数，不等同服务端缓存 eligible token 或实际命中率。

离线检查：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -m pytest -q exam_project/cache_comparison/test_driver.py
```
