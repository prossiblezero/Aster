"""Offline paired UI workflow driver. Wall time is NOT a human usability measurement."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def identity(source):
    files = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((source / "src/aster").rglob("*.py"))}
    return {"commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
            "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), "files": files}


async def run_case(args, workflow, variant, Fake):
    from textual.widgets import OptionList

    from aster.config import ModelProfile, Settings
    from aster.schema import TaskSpec, ToolCall
    from aster.ui.app import AsterTui

    root = args.output / f"{workflow}-{variant}"
    root.mkdir()
    profile = ModelProfile(provider="offline", model="base", protocol="chat", base_url="http://localhost:9/v1", auth="none", context_tokens=24000, output_tokens=512)
    settings = Settings(completion="off", approval="auto", profiles={
        "base": profile, "small": profile.model_copy(update={"model": "small", "context_tokens": 16000}),
    }).select_profile("base")
    app = AsterTui(root / "workspace", settings)
    actions = []
    extra_context = 0
    required_external = False
    Fake.reply_text = "已记录共同约束"
    suffix = f"{variant:02d}"
    shared, branch_a, branch_b = f"共同约束_{suffix}", f"方案_A_ONLY_{suffix}", f"方案_B_ONLY_{suffix}"
    fact = f"FACT_{suffix}：旧客户端必须继续使用 version=1；新客户端可以协商 version=2，缺省值保持为 1。"

    def record(kind, text):
        actions.append({"kind": kind, "text": text})

    async def command(name, argument=""):
        record("command", name + (" " + argument if argument else ""))
        previous = app.task_worker
        app._command(name, argument)
        if app.task_worker is not None and app.task_worker is not previous:
            await app.task_worker.wait()

    async def choose(pilot, identifier, summary=False):
        await pilot.pause()
        choices = app.screen.query_one(OptionList)
        available = [choices.get_option_at_index(i).id for i in range(choices.option_count)]
        assert identifier in available, (identifier, available)
        choices.highlighted = available.index(identifier)
        record("selection", "带入摘录" if summary else "选择匹配的历史位置")
        await pilot.press("ctrl+s" if summary else "enter")
        await pilot.pause()

    async def old_resume(run_id, *, external=False):
        nonlocal required_external
        await command("/history")
        if external:
            required_external = True
            record("external_lookup", "读取 .aster/runs/*/checkpoint.json，按任务文本查找")
            matches = [p.parent.name for p in app.runtime.evidence.path("runs").glob("*/checkpoint.json")
                       if json.loads(p.read_text())["state"]["task"]["prompt"] == shared]
            assert matches == [run_id]
        else:
            assert run_id in [item[0] for item in app._saved_runs()]
        app.copy_to_clipboard(run_id)
        record("copy", run_id)
        assert app.clipboard == run_id
        app.query_one("#prompt").value = "/resume " + app.clipboard
        record("paste", run_id)
        await command("/resume", run_id)

    def location():
        r = app.runtime
        return {"run": r.state.run_id, "session": r.state.session_id,
                "entry": r.session_tree.cursor if args.variant == "candidate" else None}

    async with app.run_test(size=(110, 36)) as pilot:
        await app._run_task(shared)
        ancestor = location()
        if workflow == "locate":
            # The target is outside the old recent-20 list; all files remain available.
            for index in range(24):
                app.runtime.new_session()
                await app._run_task(f"其他工作_{suffix}_{index:02d}")
            calls_before = Fake.calls
            if args.variant == "candidate":
                await command("/sessions", shared)
                await choose(pilot, ancestor["session"])
            else:
                await old_resume(ancestor["run"], external=True)
            assert app.runtime.state.session_id == ancestor["session"]
        elif workflow in {"fork", "model", "summary"}:
            Fake.reply_text = fact
            await app._run_task(branch_a)
            if workflow == "model":
                app._command("/model", "small")
            Fake.reply_text = "开始所选路径的新工作"
            calls_before = Fake.calls
            if args.variant == "candidate":
                await command("/tree", shared)
                await choose(pilot, ancestor["entry"], summary=workflow == "summary")
            else:
                if workflow == "model":
                    await command("/model", "base")
                await old_resume(ancestor["run"])
                if workflow == "summary":
                    app.copy_to_clipboard(fact)
                    record("copy", fact)
                    assert app.clipboard == fact
                    app.query_one("#prompt").value = "请带入这条探索结论：" + app.clipboard
                    record("paste_context", fact)
                    extra_context = len(fact)
                    await app._run_task("请带入这条探索结论：" + fact)
            assert app.runtime.settings.profile == "base"
            if workflow in {"fork", "summary"}:
                # The next real request is common work, excluded from navigation action counts.
                await app._run_task(branch_b)
            prompt, _, _ = app.runtime.prepare_request()
            rendered = json.dumps(prompt, ensure_ascii=False)
            assert shared in rendered
            if workflow == "summary":
                assert fact in rendered
            else:
                assert branch_a not in rendered and fact not in rendered
        elif workflow == "restore":
            # A completed write followed by a known model failure; reconstruction is a new Runtime.
            app.runtime.start(TaskSpec(prompt=f"恢复测试_{suffix}"))
            call = ToolCall(name="write_file", call_id="completed-write", arguments={"path": "once.txt", "content": "written once"})
            assert (await app.runtime.step(call)).status == "success"
            app.runtime.finish("provider_error", failed=True)
            target = location()
            from aster.core.runtime import Runtime
            detached = Runtime(app.runtime.workspace.root, settings)
            # Test fixture: mount a freshly constructed runtime; no state is copied into it.
            if args.variant == "candidate":
                app._open_session(str(detached.workspace.root))
            else:
                app.runtime.evidence.listeners.remove(app._event)
                app.runtime = detached
                app.runtime.evidence.listeners.append(app._event)
            calls_before = Fake.calls
            if args.variant == "candidate":
                # The old view simulates a terminated process, so remove it from live navigation.
                # Re-enter through persisted data instead of selecting an in-memory Runtime.
                app._views = [view for view in app._views if view["runtime"] is app.runtime]
                await command("/sessions", f"恢复测试_{suffix}")
                await choose(pilot, target["session"])
            else:
                await command("/resume", "latest")
            assert app.runtime.state.session_id == target["session"]
            assert (app.root_path / "once.txt").read_text() == "written once"
            assert "completed-write" in app.runtime.state.used_call_ids
        elif workflow == "switch":
            calls_before = Fake.calls
            if args.variant == "candidate":
                app._command("/new", str(root / "second-workspace"))
                await app._run_task("第二个独立任务")
                calls_before = Fake.calls
                await command("/switch", "1")
                assert app.runtime.state.session_id == ancestor["session"]
                await command("/switch", "2")
                assert app.runtime.state.task.prompt == "第二个独立任务"
            else:
                second = AsterTui(root / "second-workspace", settings)
                async with second.run_test():
                    await second._run_task("第二个独立任务")
                    calls_before = Fake.calls
                    required_external = True
                    record("terminal_focus", "切回第一个终端")
                    assert app.runtime.state.session_id == ancestor["session"]
                    record("terminal_focus", "切到第二个终端")
                    assert second.runtime.state.task.prompt == "第二个独立任务"
        else:
            raise ValueError(workflow)
        output = {"workflow": workflow, "variant": variant, "passed": True,
                  "product_only": not required_external, "actions": actions, "operation_count": len(actions),
                  "manual_context_characters": extra_context, "scripted_model_calls_during_workflow": Fake.calls - calls_before,
                  "session_id": app.runtime.state.session_id, "run_id": app.runtime.state.run_id}
        save(root / "result.json", output)
        return output


async def main(args):
    import aster
    from aster.providers.client import ModelClient
    from aster.schema import ModelReply
    assert Path(aster.__file__).resolve().is_relative_to(args.source / "src"), aster.__file__

    class Offline(ModelClient):
        calls = 0
        reply_text = "离线回复"
        async def complete(self, messages, tools, **kwargs):
            Offline.calls += 1
            return ModelReply(text=Offline.reply_text, raw=[{"role": "assistant", "content": Offline.reply_text}])

    patches = [patch("aster.core.runtime.ModelClient", Offline)]
    if args.variant == "candidate":
        patches.extend([patch("aster.sessions.navigation.ModelClient", Offline), patch("aster.ui.sessions.ModelClient", Offline)])
    for replacement in patches:
        replacement.start()
    before = identity(args.source)
    results = []
    try:
        for workflow in ("locate", "fork", "model", "summary", "restore", "switch"):
            for variant in range(1, 4):
                try:
                    result = await run_case(args, workflow, variant, Offline)
                except Exception as exc:
                    result = {"workflow": workflow, "variant": variant, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
                    save(args.output / f"{workflow}-{variant}" / "result.json", result)
                results.append(result)
                print(f"{workflow}/{variant}: {result['passed']}", flush=True)
    finally:
        for replacement in reversed(patches):
            replacement.stop()
    assert identity(args.source) == before, "source changed during evaluation"
    save(args.output / "report.json", {"variant": args.variant, "source": before, "import": aster.__file__,
        "python": sys.executable, "platform": platform.platform(), "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "measurement": "scripted offline workflow; no human timing or real model quality claims", "results": results})
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=["baseline", "candidate"], required=True)
    args = parser.parse_args()
    args.source, args.output = args.source.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.source / "src"))
    sys.exit(asyncio.run(main(args)))
