# 15. 摘要放不下时，怎样读到原文的最后一页？

先读 [阶段 5 的上下文预算](README.md#5-怎样控制上下文而不裁坏当前任务) 和 [阶段 13 的历史配对](13-runtime-reliability.md)。本阶段只沿着一条链路阅读：工具返回内容 → 构造工具结果 → 裁剪正文 → 模型决定是否继续翻页。配套代码是 [tools.py](../../src/aster/tools.py) 的 `TextPage`、`text_page`，[runtime.py](../../src/aster/runtime.py) 的 `todo_list` / `skill_load` 分支，以及 [context.py](../../src/aster/context.py) 的 `_clip_tool_output`。

## 问题：提示“重新读取”，为什么仍然读不到后半段？

设想计划很长，最后一段写着“修改完成后检查旧接口兼容性”。自动上下文只容得下前半段，提示模型调用 `todo_list`。如果这个工具总是返回完整计划，而送模型前又从头截断，模型每次都只能看到相同的开头。技能正文也一样：名称已经加入 `active_skills`，不代表模型已经看过它的全部内容。

Aster 保留完整计划、待办与技能来源，同时让 `todo_list` 和 `skill_load` 支持分页。自动上下文负责呈现当前状态和摘要；需要细节时，模型沿着返回的下一页位置继续读。这个机制是固定的上下文工程，没有让 Agent 自动修改上下文组织策略。

## 问题：工具的正文和元数据为什么要分开计算？

`Runtime._append_output` 将常规工具结果整理成下面的 JSON，并作为 `function_call_output.output` 的字符串值交给协议适配器：

```json
{
  "status": "success",
  "reason": "",
  "evidence_id": "事件的哈希标识",
  "changed_paths": [],
  "content": "本次读到的文字"
}
```

`content` 是正文；其余四项是描述这次执行的元数据。`status` 表示成功、失败或被阻止；`reason` 给出具体原因；`evidence_id` 关联审计证据；`changed_paths` 列出本次修改路径。若直接截取整段 JSON，可能连正文还没出现就截完，或者得到少一个引号的无效 JSON。

当前不再设置每条工具结果的固定 token 上限；只有全局请求超预算时才分配本轮需要让出的空间。`ContextBuilder._clip_tool_output` 识别 Runtime 的完整结果结构，只缩短 `content`，随后重新序列化，保留其他字段与合法 JSON。普通原始文本仍按文字裁剪；图片结果保留现有的图文处理方式。

元数据同样消耗模型输入。`effective_context_tokens - output_tokens` 是整个请求的预算，工具定义、元数据、正文、计划状态和历史都计入其中。例如压缩时正文只保留 64 tokens，并不代表这条工具消息总共只有 64 tokens。最新交互和必需状态仍无法放入全局预算时，运行会明确停止。

## 问题：offset、limit、next_offset 分别指什么？

两个工具使用相同的分页参数：

| 字段 | 含义 |
|---|---|
| `offset` | 从第几个字符开始读，从 `0` 起；按 Python 字符串位置计算，不是字节数或 token 数 |
| `limit` | 本页最多返回多少字符，默认 `512`，范围 `1–1000` |
| `next_offset` | 下一页的实际起点；为 `null` 表示已经到达当前文本末尾 |
| `total` | 当前原文的总字符数 |

例如技能原文是“先读取，再修改。”，请求 `skill_load(name="guide", offset=0, limit=4)` 时，正文形如：

```text
{"offset": 0, "next_offset": 4, "total": 8}
先读取，
```

下一次仍调用 `skill_load`，保留 `name="guide"`，把 `offset` 改成返回的 `4`。第二页正文是“再修改。”，`next_offset` 为 `null`。

`text_page` 只按字符生成页面。Runtime 给成功结果加上 `page="text"` 标记；构造完整请求时若全局超预算，`_clip_tool_output` 才按可见连续前缀缩小页面，同时重算 `next_offset`。中文、英文和代码的 token 密度不同，实际页面可能少于 `limit`。因此必须跟随返回的 `next_offset`，不能自己执行 `offset += limit`，否则可能跳过尚未读到的文字。

`todo_list` 分页的原文是包含 `plan` 和 `todos` 的 JSON 文本。一页可能从某个字符串中间开始，单页文字不一定是完整 JSON；外面的工具结果 JSON 始终合法。宿主程序如果要还原完整对象，应从 `offset=0` 开始，去掉每页第一行的分页头，按顺序拼接文字，再解析。兼容保留的 `Outcome.data.plan` / `data.todos` 供宿主访问，不会自动作为完整内容发给模型；分页信息特意写进正文，模型才能看到。

## 动手：用字符分页翻完中文原文

以下示例不调用真实模型，也不写项目文件：

```bash
conda activate aster
python - <<'PY'
from aster.tools import text_page

source = "先读取，再修改。"
offset = 0
while offset is not None:
    page = text_page(source, offset, 4)
    print(page.content)
    offset = page.data["next_offset"]
PY
```

接着打开 [test_paged_state.py](../../tests/test_paged_state.py)，运行：

```bash
python -m pytest -q tests/test_paged_state.py
```

第一个检查拼回全部中文文字，确认没有漏页；第二个检查裁剪后 JSON 仍合法，执行状态与证据编号没有丢失；最后两个检查通过真正的 Runtime 轮次，让模拟模型从首次上下文中缺失的计划或技能尾部开始，沿 `next_offset` 读到结尾。其中游标单元检查主动把每页压到 64 tokens 来验证连续性；Runtime 测试使用 4096 的全局容量，没有单工具固定 token 上限。它们验证数据能否送达，不是在评估真实模型是否会主动选择翻页。

## 边界：选中技能、读到末页和完成任务是三件事

`active_skills` 表示本次任务选中了哪些技能，自动注入的正文仍可能只是摘要。只有从起点逐页读到 `next_offset=null`，才遍历过这份当前文本；从中间位置开始读，只能说明看过对应后缀。读过技能也不能代替完成条件或独立验证器。

`.aster/skills` 是受保护的运行目录，普通 `read_file` 不绕过这条路径限制。分页通过原有 `skill_load` 访问安全检查后的技能文件，不增加新的通用元数据读取工具。计划或待办在两次读取间被修改时，可以从 `offset=0` 重新读取；分页不承诺跨修改的不可变快照。更长的读取也会消耗步骤预算，应该按任务需要查看细节。
