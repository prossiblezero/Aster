# mdcheck：真实开发与持久记忆对照

目标是由 Aster 实现一个可直接用于检查 Markdown 本地链接的 Python CLI。操作员只提供需求、调用 Runtime 并独立验收，不替 Aster 编写产品代码。

## 实验设计

两组各从只有 `PROJECT.md` 和阶段需求、没有实现代码的相同项目开始，在各自工作区连续完成三个任务。`memory_on` 开启持久 Memory、反馈演化与自动 Dream，`memory_off` 关闭这三项；任务内 working memory 均保留。两组模型、预算、工具模式、任务提示相同，Worker 关闭以免混入并行能力影响。Memory 算法和上下文组织在实验期间冻结。

1. 实现链接检查、CLI、可安装包、README 与自身测试。
2. 新运行中增加 Markdown 标题锚点检查，保留前阶段接口。
3. 操作员将 `PROJECT.md` 中默认文档目录从 `docs` 改为 `manual`，Aster 在新运行中适配并返修。用该变更观察旧来源记忆是否失效。

不会预灌记忆、在模型提示中放独立验收代码，或把机制探针当成自主开发成绩。若发生服务故障或需要返修，将保留失败尝试并单独记录干预；不会抹掉失败后宣称一次成功。这个实验检验受支持环境中的连续任务与持久记忆，不代替联网、依赖安装或自然续聊的通用能力验收。

## 文件

- `requirements.md`：完整验收约定。
- `prompts/`：三个任务提示及初始项目约定，由操作员编写。
- `run.py`、`grade.py`：操作员的运行与独立验收程序。
- `workspaces/memory_on/`、`workspaces/memory_off/`：Aster 生成并维护的项目；以各自 README 为准。
- `runs/`：本地忽略的启动参数、完整事件、阶段快照和验收结果。
- `report.md`：实验完成后的结果及证据边界。

调用方法：

```bash
conda activate aster
python exam_project/mdcheck_memory/run.py --help
```

这两份项目及其轨迹属于评测样本，不能再作为同一任务的训练数据或与训练重叠的“未见测试集”。
