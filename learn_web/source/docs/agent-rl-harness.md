# 用 Aster 做 Agent SFT / RL：token 级 rollout 接口

本文说明 `src/aster/training/` 中已经实现的训练接口，以及还没有实现的部分。路线和判断见 [v14 与 Agent RL 方案](v14-and-agent-rl-plan.md) 的 B 部分。

## 1. 为什么要做到“token 进、token 出”（token-in-token-out）

多轮 RL 的每个样本由三部分组成：策略看到的 prompt token、它生成的 token，以及这些 token 在行为策略下的 logprob。如果先把生成结果解码成文本，再交给 OpenAI 兼容服务的工具调用解析器重新序列化，最后再分词，训练用的 token 就和采样时的不一样了，重要性采样和异步训练的修正都会出错。SkyRL 和 verl 的文档都把“不对解码后的文本重新分词”列为硬性要求。

因此 Aster 的做法是：

1. 用策略模型自己的 chat template 渲染每次请求（`tokenizer.apply_chat_template`）；
2. 把 token id 作为 prompt 直接送去生成，生成结果按 id 读回；
3. 解码后由 Aster 自己解析工具调用；
4. 每个 assistant 回合记录 `prompt_ids`、`completion_ids`、`logprobs` 和 loss mask。

## 2. 组成

| 部分 | 位置 | 作用 |
| --- | --- | --- |
| `training_settings()` | `training/rollout.py` | 训练配置档：关闭 memory、后台整理、worker、审批、完成门禁和状态提示（`workflow_notes=False`），使用 chat 协议、不流式输出。奖励只来自独立的 verifier |
| `parse_completion()` | `training/qwen.py` | 解析两种工具调用格式：hermes JSON（Qwen2.5、Qwen3）和 XML 参数格式（Qwen3-Coder、Qwen3.5）。XML 参数按工具 schema 转换类型；处理 `<think>`，包括由模板在 prompt 中打开的情况；格式错误或输出被截断时如实报错，不猜测 |
| `AsterEnv.step_turn()` / `step_invalid()` | `training/env.py` | 前者处理“一段文本加多个工具调用”的一次决策，只消耗一个 turn；后者处理无法解析的输出，把错误作为下一次观察交给模型，episode 不中断 |
| `VLLMCompletions` | `training/rollout.py` | 调用 vLLM 的 `/v1/completions`，prompt 直接传 token id；打开 `return_tokens_as_token_ids` 后，生成的 token 以 `token_id:N` 的形式原样读回 |
| `rollout()` | `training/rollout.py` | 运行一个 episode，产出 `Trajectory`：逐回合的 token 数据、分段编号、奖励、终止标签，以及是否可训练 |

## 3. 分段与可训练性

- **按回合切样本永远准确**：每个回合的 loss 只计算它自己的 completion，条件就是它当时看到的 prompt。
- **分段（segment）**：如果下一次的 prompt 不是“上一次的 prompt 加上 completion”的严格延长，就开始新的一段。以下情况会触发新的一段：
  - 上下文被压缩，或旧输出被归档；
  - chat template 改写了历史，例如 Qwen3 会删掉历史回合中的 `<think>`；
  - Aster 规范化了工具调用的格式。

  同一段内的回合可以拼成一条序列训练，以提高效率；跨段的回合只能各自作为独立样本。观察中的 `context_compacted` 字段标出了上下文被改写的回合。
- **终止标签**：

  | 标签 | 是否进梯度 |
  | --- | --- |
  | `success` | 是 |
  | `candidate_failure` | 是 |
  | `parse_error` | 是 |
  | `budget_exhausted` | 否（预算或上下文达到上限） |
  | `infrastructure_error` | 否（推理服务、沙箱或 verifier 崩溃；不给奖励，即 `reward=None`） |

  预算耗尽的轨迹默认也不进梯度，这对应 DeepSWE 的 compact filtering。

## 4. 用法示意（需要 GPU 和 vLLM，尚未在本机实测）

```python
from transformers import AutoTokenizer
from aster.training.env import AsterEnv
from aster.training.rollout import VLLMCompletions, rollout, training_settings

tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-4B")
generate = VLLMCompletions("http://127.0.0.1:8000/v1", "Qwen/Qwen3.5-4B", max_tokens=4096, temperature=1.0)
env = AsterEnv(task_repo, training_settings(model="Qwen/Qwen3.5-4B", base_url="http://127.0.0.1:8000/v1",
                                            context_tokens=32768, output_tokens=4096),
               verifier=hidden_test_verifier)
trajectory = await rollout(env, task_prompt, tokenizer, generate, task_id="...")
```

## 5. 还没有做的部分

| 项 | 状态 |
| --- | --- |
| R0 冒烟测试：在 5090 上验证 vLLM、Qwen3.5 的 token 往返，以及训练侧重算的 logprob 与采样时的差异 | 需要 GPU |
| R4 容器化环境服务：`AsterEnv` 放进任务容器，提供并行 episode 和预热池；奖励器在判分前恢复测试文件、删除 git 历史并禁网 | 未实现，可复用评测 runner 的容器和 tarpit 判分 |
| R5 教师轨迹采集与拒绝采样 | 未实现，需要 API 预算（1,000 题、每题 2 条约 180 美元） |
| R6 SFT 训练 | 未实现，计划使用 TRL 或 LLaMA-Factory 的 LoRA |
| R7 RL（GRPO/DAPO）对接 SkyRL 或 verl | 未实现，先做一个小规模试用再选定框架 |
