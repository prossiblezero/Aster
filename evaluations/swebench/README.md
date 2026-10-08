# SWE-bench Verified 固定子集

[selection.json](../../datasets/swebench-verified/selection.json) 保留 2026-10-08 选择的十题：instance ID、公开问题描述、上游仓库、base commit、难度及参考补丁哈希。它不是完整 Verified，也不构成官方榜单成绩。

公开入口采用 [SWE-bench 官方 Docker harness](https://github.com/SWE-bench/SWE-bench)，不依赖维护者的 Conda 环境、历史缓存或本机日志解析器。运行成本、磁盘、Docker 架构和网络要求以官方文档为准。Aster 的基础安装不包含这个可选 harness。

## 生成候选

逐题在全新上游 checkout 中运行 Aster，只将 `problem_statement` 提供给 Agent。以清单第一题为例：

```bash
mkdir -p artifacts/swebench/patches
git clone https://github.com/django/django.git artifacts/swebench/django-15382
git -C artifacts/swebench/django-15382 checkout --detach 770d3e6a4ce8e0a91a9e27156036c1985e74d4a3
python - <<'PY'
import json
from pathlib import Path
rows = json.loads(Path('datasets/swebench-verified/selection.json').read_text())['instances']
row = next(row for row in rows if row['instance_id'] == 'django__django-15382')
Path('artifacts/swebench/problem.txt').write_text(row['problem_statement'])
PY
aster --env-file .env --cwd artifacts/swebench/django-15382 \
  "$(cat artifacts/swebench/problem.txt)"
git -C artifacts/swebench/django-15382 diff --binary HEAD > artifacts/swebench/patches/django__django-15382.patch
```

先准备目标仓库对应的任务依赖，或让 Aster 完成可执行的检查；当前示例没有自动建立旧 Django 的测试环境。最终正确性由官方隔离评分判断。新文件要先 `git add -N path/to/new_file` 才会进入上述 diff。对其他题使用各自的仓库与 base commit，不能复用已经修改过的工作区。

失败尝试也保留对应 `.patch`（没有修改时为空），另在本地记录停止原因和用量。未运行的题不应伪装成成功或从计划分母里消失。

```bash
python evaluations/swebench/predictions.py --model-id aster-your-model-revision
```

导出器验证题号并生成官方格式的 `predictions.jsonl`，没有 gold patch 或历史模型预测。

## 官方评分

在独立环境按官方文档安装 `swebench`、准备 Docker 后运行：

```bash
cd artifacts/swebench
python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Verified \
  --predictions_path predictions.jsonl \
  --max_workers 1 --run_id aster-verified-subset
```

数据由官方 harness 加载；本文没有捆绑 Docker 镜像或旧仓库依赖。首次发布只验证了导出器与固定题目契约，未重跑付费模型或官方 Docker 全流程。历史本机原生评分结论不能替代这条路径的成绩。输出、日志、镜像和工作区全部留在本地。
