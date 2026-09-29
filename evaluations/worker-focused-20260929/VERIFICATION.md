# 本轮实现验证

开始前保存标签 `pre-worker-focused-20260929` → `99eacd8`；生产修改冻结于 `856345e`。独立 worktree `/tmp/aster-worker-focused-source-20260929` 同时用于 Solo / Worker，两组使用完全相同生产代码。

- Conda `aster` 显式 `PYTHONPATH=src`：实际导入 `/home/dzh/Aster/src/aster/__init__.py`。
- 全量回归：1067 passed、4 skipped，107.83 秒。4 项 Go 在 aster 派生环境补跑全部通过（9.15 秒），不重复计入用例数。
- Ruff、Git whitespace 检查、learn_web 重建和站点检查通过；第 13 章离线示例通过。
- 独立审阅发现并修复：续问未携带最新复现条件；仅靠原生控制台摘要可能错误提升正式验证。前者从当前分支祖先链取真实用户轮，后者捕获测试框架实际结果。
- [真实原生入口记录](NATIVE-VERIFICATION.json)：Django 39 通过，缺失标签正确失败；SymPy 13 通过，75 通过伴随 3 异常正确失败。没有将旧故障工作区改成成功样本。
- wheel 安装至 Conda aster 派生环境，54 个 Python 文件逐字节匹配当前源码，实际从 site-packages 导入，排除 editable 误测。CLI help、真实 PTY 中 TUI 启动、历史选择和 quit 正常退出均通过。
- wheel SHA-256：`7f03c5d95c1e8abaf65c53728fb1819c10f9b4eb4889ab7fa227c7f2d8e96e46`。

| src/aster 指标 | 改前 99eacd8 | 改后 856345e |
| --- | ---: | ---: |
| Python 文件数 | 54 | 54 |
| 物理行（含空行与注释） | 11196 | 11351 |
| 非空行（仍含注释） | 10355 | 10509 |

生产净增 155 物理行（约 1.4%），其中原生测试支持净增 110 行；没有新增生产文件或运行依赖。复用 unittest 结果序列化、既有会话树、检查点、评测数据和评分器。

边界：分工提示不保证没有重复劳动；旧 portable 检查点没有树时只能自动携带初始请求，其余历史事实仍需父任务传递；自定义且未被跟踪的 unittest resultclass 不获正式通过。SymPy 关闭额外 hash 随机化子进程以捕获实际 reporter，保留 Aster 隔离及原生 -C 选项；两组一致。全部重测试和安装检查在真实模型评测前结束。
