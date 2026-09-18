# 缓存资格校准与布局机制对照

这是事先冻结的小预算请求实验，不是 coding 能力评测，不把模型自报完成当作项目交付。本轮先核实当前网关条件能否读取缓存，再比较动态状态放在历史前后产生的差异。旧实验的全零结果保留，不为获得正结果补跑。

## 固定协议

- 模型 `gpt-5.6-sol`，Responses，沿用本地 `.env` 的地址与认证。地址和凭证仅在内存中使用，结果只留组合 SHA256；不切换供应商，不增设 cache key 或 reasoning 参数。
- 冻结产品 Git 提交；工具定义、system 指令和请求构造器来自该提交。输入是明确标记为 synthetic 的固定多轮读文件轨迹，包含完整的调用/结果配对；不执行任何工具，不把返回内容回填后续请求。
- 每请求最多 12,000 个本地 `cl100k_base` wire JSON tokens、256 输出 tokens、90 秒，一次 HTTP POST，不自动重试、不跟随重定向。短标记的输出上限不能外推为 coding 任务应采用的上限。
- 总预算最多 **18 POST**。任何请求失败也消耗一次，不追加、不替换；进程中断后拒绝重新开始同一实验。
- 校准发送 **6 次完全相同的完整请求**。相邻请求在上一次完成后分别等待 **3、3、15、3、15 秒**。第 1 次是基线，不保证冷缓存；第 2–6 次中至少一次 HTTP/终态/短标记均有效且明确 `cached_tokens > 0`，才算建立阳性。全部零、未知或失败则停止，不进行布局 A/B。
- 阳性成立才发送三个预定区组，每组顺序 **A₀、B₀、B₁、A₁（ABBA）**，共 12 次，完成后间隔统一 3 秒。A 把动态 workflow 放在任务和历史前，B 放在历史后；同一对 A/B 的消息、工具、所有参数和动态内容完全相同，唯一差异是动态消息的位置。下标 0/1 表示一次预先固定的状态变化。各区组只更换中性的任务命名空间以减少跨区组历史缓存残留，固定历史正文保持一致。
- 校准与布局使用不同任务命名空间；不会宣称能够清空服务端共享缓存。固定 ABBA 只降低部分顺序影响，三个区组仍不足以推断一般统计显著性。

## 记录与解释

保存精确发送的 UTF-8 JSON、哈希、本地 tokens、条件及顺序、实际间隔、HTTP 状态、响应模型与 response ID、白名单响应头、原始 usage、标准化 usage、终态与短标记。字段缺失保留未知，真实零单独统计。错误只保存类别，不保存可能包含地址或凭证的异常正文；响应元数据也进行凭证与地址脱敏。

`prefix` 只衡量本地 wire JSON 的公共 token 前缀。服务端会重新组织工具、system 和消息，可能还有隐藏指令、不同 tokenizer 或缓存路由，因此不把该值当作服务端 eligible tokens。响应头只是诊断线索，没有对应字段不能认定未发生缓存。

报告先给校准逐请求结果。阳性成立后，逐区组给出第二次状态的 A₁/B₁ 缓存 tokens、真实输入及耗时，同时保留全部 A₀/B₀ 基线。三个配对差值只描述本实验，不声称真实 coding 任务提速、价格节约或 Memory/Skills 收益。

## 执行

准备与离线测试不访问模型。必须等本轮重构的冻结提交确定后再准备并调用：

```bash
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -B evaluations/benefit-20260918/cache/run.py prepare --source-commit FULL_SHA
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -B evaluations/benefit-20260918/cache/run.py run
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -B -m pytest -q -p no:cacheprovider evaluations/benefit-20260918/cache/test_driver.py
```

默认读取 `/home/dzh/Aster/.env`。prepare 从指定 Git 提交导出产品源码到临时目录并校验哈希；manifest 记录提交和源码清单，运行时重新从同一提交导出核对，避免工作区后续改动进入实验。`--env-file` 可指定同一环境文件，正式运行会校验连接指纹，拒绝悄悄切换配置。
