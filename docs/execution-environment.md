# 执行环境与 Node 正式验收

日期：2026-09-18。本批先修授权 DNS，并接入 Node 内置测试运行器；依赖跨命令复用仍未包含在这一阶段。

## 授权联网的系统读取

`network` 默认仍为 `false`。显式开启后，Landlock 只增加 `/etc/resolv.conf`、`/etc/hosts`、`/etc/nsswitch.conf`、`/etc/gai.conf` 四个已有文件的只读权限。符号链接先解析到真实文件，因此本机 `/etc/resolv.conf` 指向的 `/run/systemd/resolve/stub-resolv.conf` 可读；没有放开 `/etc`、`/run` 整个目录，也没有系统写权限。现有证书路径继续使用原有只读授权。

没有自动继承宿主代理、私有证书、凭证或新增抓取服务。需要代理的网络仍须后续增加明确配置；此次官方站验证不能代表所有企业网络均可用。

本轮只读探针于 `2026-09-18T08:48:17Z` 使用 Aster Python，请求相同 URL：<https://docs.python.org/3.12/library/venv.html>。宿主返回 HTTP 200；`network=true`、`sandbox=required` 返回 HTTP 200（1,147.82 ms，`landlock+seccomp`）；禁网沙箱失败，未收到网页。没有安装依赖或调用模型。这是基础联网检查，不是 Aster 自主调研成绩。

## Node 正式验收

`run_tests` 现在接受 `node --test [文件及参数]`。它使用 Node 内置 JUnit reporter，复用现有结构化报告解析与工作区版本绑定；至少一个已执行的测试通过，且没有失败，才建立正式验证证据。全跳过、全 TODO、空测试文件、启动错误、丢失报告、测试期间工作区变化都不能误报通过。

测试运行在独立副本，只有 Aster 随机命名的报告可返回；候选源码修改不会回写工作区。用户不能覆盖报告选项。`npm test`、其他 JS 框架与复合构建命令仍交给 `run_shell`，不会仅靠 exit 0 建立正式验证；先构建，再单独执行正式测试。

本机 Node `v18.19.1` 已完成 required 沙箱验证。使用的是 [Node 18 官方内置测试与 JUnit reporter](https://nodejs.org/docs/latest-v18.x/api/test.html#test-reporters)，未增加 JS 测试依赖。Node 会把没有声明测试的文件本身报告为通过，Aster 保守排除名字为绝对路径的通过项；支持范围是显式 `node:test` 用例。普通用户测试与 Python 一样属于工作区验证，并不因此成为抗作弊的独立训练评分器。

本轮验证：

```bash
/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_node_verification.py tests/test_network_system_files.py \
  tests/test_sandbox_packaging.py tests/test_sandbox_async.py tests/test_tool_contracts.py
```

结果：97 passed。新增 Node 正式验收和 resolver 权限测试均为离线探针；官方网页检查单独记录，没有计入自动测试成功数。
