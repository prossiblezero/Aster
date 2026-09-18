# 来源与验收边界

本项目由真实公开 Aster CLI 从空目录编写。实现、产品 README 和测试均由 Aster 生成；操作员只提供冻结需求、一次完整澄清、权限批准及一次针对性产品返修反馈，没有手工补写产品代码。

初次运行先通过 `ask_user` 澄清，随后因框架把工具 schema 错误当成致命历史错误而停止，未写入产品文件。修复框架后恢复同一会话，开发期间还出现3次约120秒的传输超时，均未报告usage。首次独立产品验收8/9，暴露删除最后课程再生成残留旧计划；原自测重复编写算法，没有测试应用本身。这些失败均保留，不能将最终交付称为一次自主成功。

唯一产品返修修复空课程重排，并将自动测试改为读取执行真实 `app.js`、使用最小 DOM 替身触发删除/生成，检查保存状态及重新加载后的结果。

- 最终运行：`a199482750d44c8186c69c6be397170d`，`completed / verified`；源码版本 `b405f30658a5be99e160d6ab16790968d43a1a15`。
- 最终自测 `node --test tests/verification.test.js`：2项通过，操作员按 README 重跑同样通过。DOM替身不是浏览器。
- 最终独立浏览器验收9/9：包括排程、截止日期/每日容量、不足提示、编辑删除重排、刷新持久化、390px手机操作及无外部联网；这是操作员验收，不是 Aster 自主浏览器验证。
- 三次运行累计42工具步、36模型轮、1196.469秒，未超过冻结70步/40轮/1200秒预算。已知用量1,262,172 tokens；3次超时的未报告用量未知，不能据此推算实际总成本。
- 五个产品文件与最终候选逐字节一致；仅 `ORIGIN.md` 和 `DELIVERY-MANIFEST.json` 是操作员新增的来源记录。缓存、临时探针、轨迹和模型设置不随产品交付。

使用与部署见 [README.md](README.md)。可直接作为静态站点预览，未代用户创建GitHub仓库或发布Pages。

证据：[冻结协议](../../evaluations/v0.2-release/study-protocol.md)、[首次8/9](../../evaluations/v0.2-release/study-initial-product.json)、[唯一返修反馈](../../evaluations/v0.2-release/study-repair-feedback.md)、[最终9/9](../../evaluations/v0.2-release/study-final-product.json)、[交付摘要](../../evaluations/v0.2-release/study-delivery.json)、[文件清单](DELIVERY-MANIFEST.json)。
