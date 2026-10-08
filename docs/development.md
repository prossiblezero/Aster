# 开发与发布验证

从干净 clone 创建 Python 3.12+ 虚拟环境，安装 `python -m pip install -e '.[dev,docs]'`。Ubuntu/Debian 需要 `python3-venv` 和 `libseccomp2`，内核需支持 Landlock。首次 tokenizer 使用可能下载词表。

完整回归还需要 `sudo apt-get install -y nodejs golang-go`。Node/Go 必须在 [Aster 的命令环境](execution-environment.md#工具链安装位置)中可用；CI 显式检查这两项，本地缺少时对应测试会跳过。

```bash
python -m ruff check src tests scripts evaluations learn_web/build.py learn_web/tests
python -m pytest -q
python docs/learning/run_examples.py 00
python scripts/benchmark.py --task debug-and-fix --env-file /dev/null --output artifacts/quickstart
```

这些命令不调用真实模型。开发依赖包括用于沙箱离线安装回归的 setuptools 和 wheel。网站测试会从教程构建页面，输出被 Git 忽略；比较器测试使用合成输入，不依赖历史实验结果。

日常可选 `python -m pytest -q -m smoke` 或 `-m core`。完整范围与按模块定位见 [tests/README.md](../tests/README.md)。

## 安装包

```bash
python -m pip install build
python -m build
python -m venv /tmp/aster-wheel-check
/tmp/aster-wheel-check/bin/python -m pip install dist/aster_harness-*.whl
cd /tmp
/tmp/aster-wheel-check/bin/aster --help
/tmp/aster-wheel-check/bin/aster-tui --help
```

wheel 只包含 `aster` 运行包；评测、fixture、教程从 Git clone 获取。验证时检查 `aster.__file__` 指向新环境的 `site-packages`，避免源码目录遮蔽安装错误。

## 文件维护

- 生产代码放 `src/aster/`；工程测试放 `tests/`。
- 固定题目、fixture、评分标准和来源元数据放 `datasets/`；执行器放 `scripts/` 或 `evaluations/`。
- `artifacts/`、`.aster/`、下载的上游仓库、预测、模型轨迹和生成网站都是本地产物，不提交。
- 不将 `.env`、个人简历、机器路径、冻结环境快照和逐轮成绩表放进公开源码。
- 设计变更更新相关使用文档；“遇到什么问题、如何解决、为什么”更新[优化历程](optimization-history.md)。

首次公开版本采用干净源码快照，避免旧提交携带评测产物。历史开发分支和原始实验仍保留在维护者本地；公开仓库中的开发时间线不意味着那些旧 Git 对象也已上传。
