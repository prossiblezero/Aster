# 项目 Skills

Aster 支持项目内技能包与旧式 Markdown 技能。技能提供任务说明，不改变工具权限、Shell 沙箱、写入版本检查或验收要求。

```text
.aster/skills/review/SKILL.md
.aster/skills/review/references/checklist.md
.skills/review/SKILL.md
.skills/legacy.md
```

同名时 `.aster/skills` 优先；同一来源内目录式 `name/SKILL.md` 优先于 `name.md`。选定来源后，正文和资源始终属于该来源；缺少资源或元数据无效时不会偷偷改用另一份同名技能。没有隐式扫描用户级目录，也不会导入操作员的技能。

## 元数据与正文

`SKILL.md` 可以包含以下 frontmatter；旧式 Markdown 也支持同样格式。

```markdown
---
name: review
description: >-
  Review local patches for correctness,
  boundary checks and useful regression coverage.
---
Read references/checklist.md before reviewing the patch.
State concrete findings with file paths and evidence.
```

`name` 可省略；存在时必须与文件别名或技能目录名一致。`description` 用于发现和选择技能，最多 1000 字符；不含 frontmatter 的旧文件仍可加载，描述为空。技能名称保持现有 ASCII 字母、数字、下划线、点和短横线格式，长度 1–80；`.`、`..`、受保护或敏感名称无效。

为避免新增解析依赖，仅支持这一明确子集：

- `name`、`description` 的普通字符串、JSON 双引号字符串及使用 `''` 转义的单引号字符串。
- 使用空格缩进的 `|`、`|-`、`|+`、`>`、`>-`、`>+` 多行字符串。字面量保留内部换行，折叠格式合并相邻非空行；首尾空白统一去除，不实现完整 YAML 的 chomping 规则。
- 空行和独立注释行。未知字段及其缩进内容忽略，不能授予权限；不解释 YAML 对象、数组、标签或锚点。

已知字段重复、空值、布尔/数字/空类型、名称不匹配、未闭合引号/头部等会使该技能不可加载。frontmatter 总量最多 4 KiB，整个文件最多 20 KiB。错误会在目录记录的 `error` 中给出，不会把无效技能当作可用技能静默加载。

## 惰性读取 API

`Skills.catalog()` 只读有界头部和资源文件名，不读取正文及资源内容。每条记录包括 `name`、`description`、项目相对 `path`、目录式技能的 `base_dir`、`resources` 和 `resources_truncated`；旧式技能的 `base_dir` 为 `None`。`Skills.list()` 保持原 API，返回可加载的名称，不包含带 `error` 的记录。

`Skills.load(name)` 显式读取正文并剥去 frontmatter。`Skills.load(name, resource="references/checklist.md")` 读取所选包内的 UTF-8 文本资源；旧式文件没有包资源。脚本文件也只作为文本返回，**不会执行**。资源路径以该包目录为基准，不能借此读取其他 `.aster` 元数据。后续执行脚本仍需使用原有执行工具并通过权限检查；这项支持没有开放 `.aster` 给 Shell。

Runtime 在动态上下文展示上述目录元数据，不提前注入正文或资源。通过 `tool_load` 加载 `skill_load` 后，可用 `name` 选择正文，或附 `resource` 读取资源；二者都支持 `offset/limit` 字符分页。正文选择会记录 `active_skills`，资源读取不会激活技能或替换已选正文。先对完整内容脱敏再分页，保持页间位置一致。目录、正文和资源始终是非授权的参考资料。

正文与每个资源最多 20 KiB；按字节计算。读取拒绝绝对路径、`..`、反斜杠、冒号、NUL、符号链接及特殊文件，也拒绝 `.env`、私钥和原有受保护/忽略目录。读取与目录枚举均使用不跟随符号链接的目录描述符，避免父目录在检查后被替换为链接而重定向访问。

发现阶段每个来源最多检查 256 个直接条目；超过上限会明确报错。每个包最多扫描 128 个资源条目、列出 64 个资源，最深扫描三层子目录，达到边界时设置 `resources_truncated`。截断仅限制目录展示；已经知道的合法资源路径仍可按需读取。资源目录不会列出符号链接、敏感文件、特殊文件或超过大小上限的文件。

## 验证边界

`tests/test_skills.py` 与 `tests/test_skill_packages.py` 覆盖旧格式、元数据、来源优先级、按需资源、大小限制、路径隔离、符号链接及目录替换竞争。实际 Runtime 的目录注入、分页、激活版本与真实模型选择效果分别由集成测试和独立实战报告验证；以上离线测试不代表已经证明技能能提高任务成功率。
