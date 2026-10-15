# 执行环境与 Node 正式验收

日期：2026-09-18。授权 DNS 和 Node 内置测试运行器先独立交付；随后增加默认关闭的受控依赖复用。两阶段的检查分别记录。

## 工作区绝对路径与沙箱安装

2026-10-11 修复工作区路径不一致：此前 `read_file` 可以读取 `/app/interp.py`，Shell 却在 `/tmp/aster-exec-*` 执行，`cd /app` 会指向被 Landlock 拒绝的原目录。这是 Aster 的执行环境缺陷。

现在用 bubblewrap 在私有挂载命名空间中，将过滤后的执行副本映射到原工作区路径。`cd /app`、`python /app/interp.py`、配置内的绝对路径和子进程都指向同一副本；正式 `run_tests` 也保留原路径。没有改写命令字符串，也没有把命令的写权限直接授予宿主项目。敏感文件与控制目录继续在复制时排除，Landlock/seccomp、只读模式、写入范围、并发版本检查和回写校验继续生效。其余宿主文件系统只读挂载，实际读取仍受 Landlock 限制。每条命令有独立的可写 `TMPDIR`，临时测试目录不会继承项目配置。

需要 Linux Landlock ABI ≥ 3、libseccomp、支持 `--json-status-fd` 的 bubblewrap，以及创建用户/挂载命名空间的权限。Debian/Ubuntu 安装与检查：

```bash
sudo apt-get install bubblewrap libseccomp2
bwrap --ro-bind / / -- true
```

Ubuntu 24.04 开启 `apparmor_restrict_unprivileged_userns` 时，若检查报 `setting up uid map: Permission denied`，管理员可安装本仓库的程序级许可：

```bash
sudo install -m 644 scripts/aster-bwrap.apparmor /etc/apparmor.d/aster-bwrap
sudo apparmor_parser -r /etc/apparmor.d/aster-bwrap
bwrap --ro-bind / / -- true
```

该 profile 仅为 `/usr/bin/bwrap` 放行用户命名空间，不关闭全局 AppArmor 限制。若发行版已提供 bubblewrap profile，应由管理员在已有配置中处理许可，避免重复的执行路径绑定。容器还须允许 bubblewrap 创建挂载命名空间；仅在容器中安装软件包不一定足够。默认 `sandbox="required"` 在依赖或命名空间不可用时返回 `sandbox_unavailable`，命令不会执行。

本次修复不回填既有 benchmark 的分数；2026-10-11 第二轮的 Aster 15/20 保持原始记录。路径回归与解释器启动检查属于工程验证，不能直接计为整题独立验收通过。

## 授权联网的系统读取

`network` 默认仍为 `false`。显式开启后，Landlock 只增加 `/etc/resolv.conf`、`/etc/hosts`、`/etc/nsswitch.conf`、`/etc/gai.conf` 四个已有文件的只读权限。符号链接先解析到真实文件，因此本机 `/etc/resolv.conf` 指向的 `/run/systemd/resolve/stub-resolv.conf` 可读；没有放开 `/etc`、`/run` 整个目录，也没有系统写权限。现有证书路径继续使用原有只读授权。

v0.2 真实依赖任务另补 `/etc/mime.types` 已有真实文件的精确只读授权，供 Python 标准库 `mimetypes` 和 openpyxl 使用；这项本地元数据读取不要求联网，不授予系统目录或写权限。首测的导入失败和修复见[环境问题记录](../evaluations/v0.2-release/engineering-issue-02.md)。

没有自动继承宿主代理、私有证书、凭证或新增抓取服务。需要代理的网络仍须后续增加明确配置；此次官方站验证不能代表所有企业网络均可用。

本轮只读探针于 `2026-09-18T08:48:17Z` 使用 Aster Python，请求相同 URL：<https://docs.python.org/3.12/library/venv.html>。宿主返回 HTTP 200；`network=true`、`sandbox=required` 返回 HTTP 200（1,147.82 ms，`landlock+seccomp`）；禁网沙箱失败，未收到网页。没有安装依赖或调用模型。这是基础联网检查，不是 Aster 自主调研成绩。

## Node 正式验收

`run_tests` 现在接受 `node --test [文件及参数]`。它使用 Node 内置 JUnit reporter，复用现有结构化报告解析与工作区版本绑定；至少一个已执行的测试通过，且没有失败，才建立正式验证证据。全跳过、全 TODO、空测试文件、启动错误、丢失报告、测试期间工作区变化都不能误报通过。

正式测试报告在隔离副本销毁前读取。测试因越出写入范围而返回 `blocked/read_only` 时，仍保留有效报告中的失败，避免后续只跑部分测试就遗忘旧失败；受阻执行中的通过项不能清除失败或建立验证通过。这个读取不会放行测试生成的其他文件。

测试运行在独立副本，只有 Aster 随机命名的报告可返回；候选源码修改不会回写工作区。用户不能覆盖报告选项。`npm test`、其他 JS 框架与复合构建命令仍交给 `run_shell`，不会仅靠 exit 0 建立正式验证；先构建，再单独执行正式测试。

本机 Node `v18.19.1` 已完成 required 沙箱验证。使用的是 [Node 18 官方内置测试与 JUnit reporter](https://nodejs.org/docs/latest-v18.x/api/test.html#test-reporters)，未增加 JS 测试依赖。Node 会把没有声明测试的文件本身报告为通过，Aster 保守排除名字为绝对路径的通过项；支持范围是显式 `node:test` 用例。普通用户测试与 Python 一样属于工作区验证，并不因此成为抗作弊的独立训练评分器。

本轮验证：

```bash
/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_node_verification.py tests/test_network_system_files.py \
  tests/test_sandbox_packaging.py tests/test_sandbox_async.py tests/test_tool_contracts.py
```

结果：97 passed。新增 Node 正式验收和 resolver 权限测试均为离线探针；官方网页检查单独记录，没有计入自动测试成功数。

## 受控依赖复用

在用户自己的 Aster TOML 配置中设置 `reuse_environment = true`，并保留 `sandbox = "required"`。默认关闭；复用不支持关闭沙箱或降级运行，隔离不可用时直接拒绝。开启后，每个 `SandboxRunner` 单独持有进程内的稳定临时执行目录，跨命令只保留 `.venv` 与 `node_modules`；受管理源码每次从当前工作区重新同步，继续校验并发修改、写范围和文件版本。依赖不回写项目源目录，不向主工作区的 `.aster` 或宿主 Python 环境安装内容。

子进程仍使用 Aster 的 Python。`PIP_TARGET` 和 `PYTHONPATH` 指向临时执行根内 `.venv/lib/python<版本>/site-packages`，因此普通 `python -m pip install` 的目标受限于沙箱，后续命令可导入该处依赖。没有激活、替换或改写全局解释器；是否允许包下载仍由 `network` 和审批控制。Node 按标准规则使用执行根中的 `node_modules`。

`PIP_CACHE_DIR`、`XDG_CACHE_HOME` 指向执行根的 `.tmp/cache`，不把包缓存回写为项目源码。pip 在检查宿主 editable 包元数据时仍可能遇到沙箱拒绝并打印冲突检查警告；本轮 openpyxl 安装退出成功且后续独立命令和正式测试均能导入。没有为了消除诊断而开放宿主源码路径。真实使用及独立干净环境重建证据见[v0.2验收](../evaluations/v0.2-release/REPORT.md)，产品质量与安装成功分别判断。

正式 pytest/unittest 先在 `-I` 模式导入可信运行器，再将候选源码放在搜索路径首位、受控依赖放在其后；同名已安装模块不能遮蔽当前候选源码，依赖中的伪造 `pytest.py` 也不能替代可信入口。测试与只读、plan、write_scope 命令使用新的源码副本，通过精确的 Landlock 只读授权借用父 Runner 依赖。借用期间还禁止 chmod/chown、时间戳和扩展属性修改，弥补 Landlock 不控制这些元数据的边界；因此借用命令中需要修改权限或时间戳的安装操作会被拒绝。普通可写命令保持原有权限位写入行为。

依赖只允许树内符号链接，兼容 Node `.bin` 相对链接和 `.venv/lib64`。`.venv/bin/python*` 唯一的树外例外是当前 Aster Python 的真实路径；任意其他系统链接、断链和特殊文件均被拒绝。取消、非成功的可写执行、依赖校验失败或保护路径输出会销毁该执行根。同一 Runner 忙时拒绝并发调用；Worker 的 Runner 不共用可写根。

每次可写执行与环境销毁都改变环境代次，旧正式测试因此失效。该规则保守作用于所有可写 Shell 命令，包括实际上仅输出信息的命令；需要在准备交付前最后执行正式测试。`run_tests` 绑定源码摘要与借用环境代次，运行前后均须一致。

同一会话的继续保留环境；新任务、`/new`、恢复到另一个保存状态会清理原临时环境。没有跨进程持久化承诺，重启后需按锁文件重新安装并验证。独立发布或 README 重建仍须在新目录执行，不把已热好的临时依赖当成可重建性证据。

依赖复用测试使用合成 Python/Node 模块、`venv --without-pip` 的真实目录布局，以及在执行目录中临时创建的 setuptools 小包。后者通过真实 `pip --no-index --no-deps --no-build-isolation` 安装到默认 `PIP_TARGET`，后续独立命令与正式 pytest 均导入成功，宿主环境没有安装该包。没有下载外部包或调用模型。这些检查覆盖跨命令读取、正式测试借用、来源同步、符号链接边界、权限/元数据防写、Runner 隔离及取消清理；尚不能代替后续 Aster 自主安装真实依赖的实战验收。

本轮依赖专项与会话生命周期验收：`/home/dzh/.conda/envs/aster/bin/python -m pytest -q tests/test_dependency_environment.py tests/test_environment_lifecycle.py`，结果 21 passed；独立复核补充的 `tests/test_environment_review.py` 两项同名源码优先级反例通过。最终依赖、生命周期、Node 与工具契约相邻回归共 112 passed，源码发布与沙箱边界此前也已通过本轮回归，没有把历史网站成绩充作本轮结果。

实现依据：[pip 的 `--target` / `PIP_TARGET`](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-t)、[Python 官方虚拟环境的不可搬移约束](https://docs.python.org/3.12/library/venv.html)。依赖保持在稳定路径，未把现有环境复制到每个新命令目录。

## 模型可见环境与 Web 检查边界

v0.2 在稳定策略中说明实际 Python 版本、Node 版本与常用命令可用性，以及沙箱、联网、依赖复用和命令生命周期；不逐轮扫描整个系统。Shell 临时文件使用 `.tmp/` 或 `TMPDIR`。每条命令结束时清理进程组，后台 HTTP 服务不会跨工具调用保留；exit 0 也不代表服务启动成功，必须检查 stderr 和实际行为。

`run_tests` 的 Node reporter 参数由宿主保留，拒绝时会提示移除。构建、语法和浏览器检查使用 `run_shell`，与正式测试证据分别报告。浏览器 `.js` 若使用 ES module，应在 `package.json` 声明 `"type": "module"`，或采用 `.mjs`；不能拿 CommonJS 语法检查误判浏览器模块。

Playwright 是可选的项目验证依赖，不是 Aster 核心依赖。需要授权安装浏览器并允许相应网络和进程操作，服务器、浏览器和清理必须在同一条有界命令中执行；不能关闭沙箱来假装支持。当前独立浏览器验收由操作员执行，不能据此声称 Aster 已在沙箱中自主完成浏览器验证。禁网时本地 TCP 服务也不可用。
