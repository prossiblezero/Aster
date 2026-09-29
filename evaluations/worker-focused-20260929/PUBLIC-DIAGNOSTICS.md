# 补充公开样例诊断

这组诊断在 24 次真实模型运行结束后执行，只读取已完成候选工作区，没有发起模型请求，也没有修改原评分。脚本和 Linux 沙箱的哈希已记录在 [public-diagnostics.json](public-diagnostics.json)。长 stdout、stderr 和候选绝对路径保留在本地私有产物中，不进入仓库。

打印器检查把 `A - A*B - B` 分别交给 `str`、pretty 和 LaTeX 输出。9 次每臂样例中各有 1 次与预期标准形式匹配；其他形式保留为 `unknown`，因为它们可能只是等价但未被这个最小规范化器识别，不能直接当成失败。

CDF 检查 12 个公开分布，每个分布用原问题构造、首点和两个内部点，要求结果是有限概率、没有未求积 `Integral`，并与独立 Python 公式相符；Erlang 还检查精确参数没有生成 SymPy `Float`。结果如下：

| 诊断 | Solo | Worker |
| --- | ---: | ---: |
| CDF 内部样例 verified | 30/36 | 31/36 |
| CDF observed mismatch | 4 | 2 |
| CDF unknown（异常或超时） | 2 | 3 |

首轮 Worker 为 12/12、Solo 为 10/12，且 Worker 子任务确实写入了 6 个 CDF 方法；后两轮没有保持这个覆盖，仍出现 Erlang 精度问题以及 StudentT/UniformSum 超时。因此这是一条“某次有界代码协作有效”的证据，不能改写成十二种分布全部正确，也不能替代原任务分数。CDF-13878 的 Arcsin 下界静态缺陷仍由任务审计保留。
