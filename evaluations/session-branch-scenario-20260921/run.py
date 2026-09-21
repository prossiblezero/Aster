"""Focused paired evaluation for returning to dialogue i and exploring a new idea."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

I_PROMPTS = [
    "确定课程导出工具使用 SQLite 本地存储。关键约束：storage=sqlite。",
    "确定产品绝不上传用户数据。关键约束：privacy=offline_only。",
    "确定所有日期按上海时区解释。关键约束：timezone=Asia/Shanghai。",
    "确认以上三项是当前方案的硬约束，请在后续设计中始终保留。",
]
J_PROMPTS = [
    "探索 FUTURE_POSTGRES：云端 PostgreSQL 在大数据量下可能更快，但只是备选研究。",
    "探索 FUTURE_UPLOAD：上传到云端可以减少本地计算，但与 offline_only 冲突，不能直接采用。",
    "探索 FUTURE_UTC：服务端统一 UTC 可能简化部署，但会与 Asia/Shanghai 的产品约束冲突。",
]
FINAL_PROMPT = (
    "请从当前可见的历史位置继续设计新的课程导出方案。输出一个 JSON 对象，不要 Markdown，字段必须是 "
    "storage、privacy、timezone、decision。必须保留 storage=sqlite、privacy=offline_only、"
    "timezone=Asia/Shanghai。若看到了探索资料，只能把云端方案写入 decision 的 rejected_exploration，"
    "不能把它们当成当前方案。"
)
SUMMARY_PROMPT = (
    "请为新会话生成一段不超过 500 字的交接摘要。保留前三项硬约束，并说明 FUTURE_POSTGRES、"
    "FUTURE_UPLOAD、FUTURE_UTC 只是与约束冲突的探索资料。只输出摘要文本。"
)
REQUIRED = ("sqlite", "offline_only", "asia/shanghai")
FUTURE = ("FUTURE_POSTGRES", "FUTURE_UPLOAD", "FUTURE_UTC")


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def source_identity() -> dict:
    import aster

    source = Path(aster.__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted((source / "src" / "aster").rglob("*.py")):
        digest.update(str(path.relative_to(source)).encode())
        digest.update(path.read_bytes())
    return {
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip(),
        "src_sha256": digest.hexdigest(),
    }


class Echo:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools, **kwargs):
        from aster.schema import ModelReply

        self.calls += 1
        user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if SUMMARY_PROMPT in user:
            text = (
                "硬约束：storage=sqlite；privacy=offline_only；timezone=Asia/Shanghai。"
                "FUTURE_POSTGRES、FUTURE_UPLOAD、FUTURE_UTC 只是冲突的探索资料。"
            )
        else:
            text = "离线替身已记录当前请求"
        return ModelReply(text=text, raw=[{"role": "assistant", "content": text}])


def settings_for(mode, env_file):
    from aster.config import Settings, load_settings

    if mode == "real":
        settings = load_settings(Path(env_file), sandbox="required", approval="auto")
        if not settings.api_key.get_secret_value():
            raise ValueError("real mode needs configured API key")
    else:
        settings = Settings(
            provider="offline", model="offline", protocol="chat", base_url="http://localhost:9/v1",
            auth="none", completion="off", approval="auto", sandbox="required",
            context_tokens=16000, output_tokens=1024,
        )
    return settings.model_copy(update={"read_only": True, "completion": "off", "output_tokens": min(settings.effective_output_tokens, 1024)})


async def prepare_history(root, settings):
    from aster.core.runtime import Runtime
    from aster.schema import TaskSpec

    echo = Echo()
    runtime = Runtime(root, settings, model=echo)
    await runtime.run(TaskSpec(prompt=I_PROMPTS[0]))
    for prompt in I_PROMPTS[1:]:
        runtime.continue_task(prompt)
        await runtime.run()
    target_run = runtime.state.run_id
    tree = getattr(runtime, "session_tree", None)
    target_entry = tree.cursor if tree is not None else None
    for prompt in J_PROMPTS:
        runtime.continue_task(prompt)
        await runtime.run()
    return runtime, echo, target_run, target_entry


def usage_from_runtime(runtime):
    state = runtime.state
    return {
        "model_requests": state.usage.get("usage_requests", 0),
        "input_tokens": state.usage.get("input_tokens"),
        "output_tokens": state.usage.get("output_tokens"),
        "total_tokens": state.usage.get("total_tokens"),
    }


def trace_usage(root: Path, runtime) -> dict:
    traces = list((root / ".aster" / "runs").glob("*/trace.jsonl"))
    attempts = []
    for path in traces:
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get("event") == "provider_attempt":
                attempts.append(event)
    return {
        "http_attempts": len(attempts),
        "http_attempts_with_usage": sum(bool(e.get("usage_observed")) for e in attempts),
        "http_classifications": {
            label: sum(e.get("classification") == label for e in attempts)
            for label in sorted({e.get("classification") for e in attempts})
        },
    }


async def run_path(args, path_name, index):
    from aster.core.runtime import Runtime
    from aster.providers.client import ModelClient
    from aster.schema import TaskSpec

    root = args.output / f"{path_name}-{index}"
    root.mkdir(parents=True, exist_ok=True)
    settings = settings_for(args.mode, args.env_file)
    runtime, echo, target_run, target_entry = await prepare_history(root, settings)
    before_calls = echo.calls
    handoff = ""
    preparation_usage = {}
    manual_chars = 0
    preparation_model_calls = 0
    operations = {
        "legacy_summary": ["提交总结请求", "复制摘要", "新建会话", "粘贴摘要"],
        "legacy_checkpoint": ["查找 checkpoint", "复制运行编号", "粘贴运行编号", "提交 resume"],
        "branch_clean": ["打开历史树", "选择第 i 轮位置"],
        "branch_excerpt": ["打开历史树", "选择第 i 轮位置并带入摘录"],
    }[path_name]

    if path_name == "legacy_summary":
        if args.mode == "real":
            runtime.model = ModelClient(settings)
            runtime.continue_task(SUMMARY_PROMPT)
            await runtime.run()
            handoff = runtime.state.final
            preparation_model_calls += runtime.state.usage.get("usage_requests", 0)
            preparation_usage = dict(runtime.state.usage)
        else:
            handoff = (
                "硬约束：storage=sqlite；privacy=offline_only；timezone=Asia/Shanghai。"
                "FUTURE_POSTGRES、FUTURE_UPLOAD、FUTURE_UTC 只是冲突的探索资料。"
            )
        manual_chars = len(handoff)
        fresh = Runtime(root, settings, model=ModelClient(settings) if args.mode == "real" else echo)
        fresh.start(TaskSpec(prompt=FINAL_PROMPT + "\n交接摘要：\n" + handoff))
        await fresh.run()
        final_runtime = fresh
    elif path_name == "legacy_checkpoint":
        fresh = Runtime(root, settings, model=ModelClient(settings) if args.mode == "real" else echo)
        fresh.resume(target_run)
        fresh.continue_task(FINAL_PROMPT)
        await fresh.run()
        final_runtime = fresh
    else:
        if args.variant != "candidate":
            raise ValueError(f"{path_name} requires candidate variant")
        runtime.checkout(target_entry, summary=path_name == "branch_excerpt")
        if path_name == "branch_excerpt":
            manual_chars = 0
        runtime.continue_task(FINAL_PROMPT)
        if args.mode == "real":
            runtime.model = ModelClient(settings)
        await runtime.run()
        final_runtime = runtime

    prompt, _, _ = final_runtime.prepare_request()
    rendered = json.dumps(prompt, ensure_ascii=False).casefold()
    final = (final_runtime.state.final or "").casefold()
    source_text = rendered + "\n" + final
    constraints = {item: item in source_text for item in REQUIRED}
    future_in_prompt = {item: item.casefold() in rendered for item in FUTURE}
    isolation_ok = path_name in {"branch_clean", "legacy_checkpoint"} and not any(future_in_prompt.values())
    if path_name in {"legacy_summary", "branch_excerpt"}:
        isolation_ok = all(future_in_prompt.values())
    answer_ok = all(value and item in final for item, value in constraints.items())
    usage = usage_from_runtime(final_runtime)
    for key in ("input_tokens", "output_tokens", "total_tokens", "usage_requests"):
        if preparation_usage.get(key) is not None and usage.get(key) is not None:
            usage[key] += preparation_usage[key]
    if preparation_usage.get("usage_requests"):
        usage["model_requests"] += preparation_usage["usage_requests"]
    if args.mode == "scripted":
        answer_ok = all(item in rendered for item in REQUIRED)
    result = {
        "scenario": "i4_j3_course_export",
        "path": path_name,
        "variant": args.variant,
        "mode": args.mode,
        "passed": bool(isolation_ok and answer_ok),
        "constraints_preserved": constraints,
        "future_markers_in_final_prompt": future_in_prompt,
        "isolation_ok": isolation_ok,
        "answer_ok": answer_ok,
        "operations": operations,
        "operation_count": len(operations),
        "manual_handoff_characters": manual_chars,
        "preparation_model_calls": preparation_model_calls,
        "echo_setup_calls": before_calls,
        "usage": usage,
        "http": trace_usage(root, final_runtime),
        "final_text": final_runtime.state.final[:2000],
        "target_run": target_run,
        "target_entry": target_entry,
        "source": source_identity(),
    }
    write(root / "result.json", result)
    return result


async def compaction_probe(args):
    """Check that a later compacted sibling cannot rewrite the saved i boundary."""
    from aster.core.runtime import Runtime

    root = args.output / "compaction-probe"
    root.mkdir(parents=True, exist_ok=True)
    settings = settings_for("scripted", args.env_file)
    runtime, _, target_run, target_entry = await prepare_history(root, settings)
    runtime._structured_history[:] = [{"role": "user", "content": "COMPACTED_FUTURE_ONLY"}]
    runtime.checkpoint()
    if args.variant == "candidate":
        runtime.checkout(target_entry)
        restored = json.dumps(
            {"task": runtime.state.task.prompt, "history": runtime._structured_history},
            ensure_ascii=False,
        )
    else:
        restored_runtime = Runtime(root, settings, model=Echo())
        restored_runtime.resume(target_run)
        restored = json.dumps(
            {"task": restored_runtime.state.task.prompt, "history": restored_runtime._structured_history},
            ensure_ascii=False,
        )
    result = {
        "variant": args.variant,
        "target_run": target_run,
        "target_entry": target_entry,
        "original_constraint_restored": "storage=sqlite" in restored.casefold(),
        "later_compaction_isolated": "COMPACTED_FUTURE_ONLY" not in restored,
        "passed": "storage=sqlite" in restored.casefold() and "COMPACTED_FUTURE_ONLY" not in restored,
    }
    write(root / "result.json", result)
    return result


async def main(args):
    import aster

    source = Path(aster.__file__).resolve().parents[2]
    assert Path(aster.__file__).resolve().is_relative_to(source / "src"), aster.__file__
    args.output.mkdir(parents=True, exist_ok=True)
    paths = ("legacy_summary", "legacy_checkpoint") if args.variant == "baseline" else (
        "branch_clean", "branch_excerpt"
    )
    results = []
    for index, path_name in enumerate(paths, 1):
        try:
            result = await run_path(args, path_name, index)
        except Exception as exc:
            result = {"scenario": "i4_j3_course_export", "path": path_name, "variant": args.variant,
                      "mode": args.mode, "passed": False,
                      "error": f"{type(exc).__name__}: {exc}"}
            write(args.output / f"{path_name}-{index}" / "result.json", result)
        results.append(result)
        print(f"{args.variant}/{args.mode}/{path_name}: {result['passed']}", flush=True)
    write(args.output / "results.json", {"variant": args.variant, "mode": args.mode, "rows": results})
    if args.mode == "scripted":
        probe = await compaction_probe(args)
        print(f"{args.variant}/scripted/compaction_probe: {probe['passed']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--mode", choices=("scripted", "real"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", default="/home/dzh/Aster/.env")
    asyncio.run(main(parser.parse_args()))
