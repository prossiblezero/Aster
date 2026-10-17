"""Measure Runtime overhead per step with a scripted offline model (no provider calls).

Builds a synthetic repository, then drives the production Runtime through a fixed
sequence of read/search/patch/shell steps. Reports wall time per step and, with
--profile, the top cumulative functions.
"""
import argparse
import asyncio
import cProfile
import json
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aster.config import Settings  # noqa: E402
from aster.core.runtime import Runtime  # noqa: E402
from aster.schema import ModelReply, TaskSpec, ToolCall  # noqa: E402


def build_repo(root: Path, files: int, kib: int):
    line = "def function_{i}_{j}(value):\n    return value * {j} + {i}  # padding text for size\n"
    for i in range(files):
        package = root / f"pkg{i % 40}"
        package.mkdir(parents=True, exist_ok=True)
        body, j = [], 0
        while sum(map(len, body)) < kib * 1024:
            body.append(line.format(i=i, j=j))
            j += 1
        (package / f"module_{i}.py").write_text("".join(body))


class ScriptedModel:
    """Each reply carries native Responses items so history looks like a real run."""

    def __init__(self, steps):
        self.steps, self.index = steps, 0

    async def complete(self, messages, tools, **_):
        if self.index >= len(self.steps):
            return ModelReply(text="done", usage={"usage_requests": 1, "input_tokens": 1000, "output_tokens": 10},
                              raw=[{"type": "message", "role": "assistant", "id": f"msg_{self.index}",
                                    "content": [{"type": "output_text", "text": "done"}], "phase": "final_answer"}])
        name, arguments = self.steps[self.index]
        self.index += 1
        call_id = f"call_{self.index}"
        raw = [{"type": "reasoning", "id": f"rs_{self.index}", "summary": [],
                "encrypted_content": "gAAAA" + "x" * 1600},
               {"type": "function_call", "id": f"fc_{self.index}", "call_id": call_id,
                "name": name, "arguments": json.dumps(arguments)}]
        return ModelReply(calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)], raw=raw,
                          usage={"usage_requests": 1, "input_tokens": 15000 + 500 * self.index,
                                 "output_tokens": 200})


def steps(count):
    plan = []
    for k in range(count):
        path = f"pkg{k % 40}/module_{k}.py"
        kind = k % 4
        if kind == 0:
            plan.append(("read_file", {"path": path, "start": 1, "end": 200}))
        elif kind == 1:
            plan.append(("search", {"pattern": f"function_{k}_3(", "path": f"pkg{k % 40}"}))
        elif kind == 2:
            prev = f"pkg{(k - 2) % 40}/module_{k - 2}.py"
            plan.append(("patch_file", {"path": prev, "old_text": f"def function_{k - 2}_1(value):",
                                        "new_text": f"def function_{k - 2}_1(value):  # edited"}))
        else:
            plan.append(("run_shell", {"command": "ls pkg0 | head -3", "timeout": 30}))
    return plan


async def run(workspace: Path, count: int, sandbox: str):
    settings = Settings(model="gpt-5.6-luna", base_url="http://127.0.0.1:9/v1", api_key="offline",
                        stream=False, context_tokens=64000, output_tokens=8192, approval="auto",
                        sandbox=sandbox, network=False, completion="off", max_workers=0)
    runtime = Runtime(workspace, settings, approval_callback=lambda *_: True)
    runtime.model = ScriptedModel(steps(count))
    started = time.perf_counter()
    try:
        state = await runtime.run(TaskSpec(prompt="Benchmark harness overhead.", max_steps=500, max_turns=500))
    finally:
        await runtime.maintenance.close()
        runtime.sandbox.close()
    return time.perf_counter() - started, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000)
    parser.add_argument("--kib", type=int, default=18)
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--sandbox", default="required")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--git", action="store_true", help="make the synthetic repository a git repository")
    parser.add_argument("--keep", type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="aster-bench-"))
    try:
        build_repo(root, args.files, args.kib)
        if args.git:
            import subprocess
            for command in (["git", "init", "-q"], ["git", "add", "-A"],
                            ["git", "-c", "user.name=b", "-c", "user.email=b@b", "commit", "-qm", "base"]):
                subprocess.run(command, cwd=root, check=True)
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        profiler = cProfile.Profile() if args.profile else None
        if profiler:
            profiler.enable()
        seconds, state = asyncio.run(run(root, args.steps, args.sandbox))
        if profiler:
            profiler.disable()
        aster = root / ".aster"
        stored = sum(p.stat().st_size for p in aster.rglob("*") if p.is_file())
        print(json.dumps({"repo_mib": round(size / 2**20, 1), "steps": state.step, "phase": state.phase,
                          "stop_reason": state.stop_reason, "seconds": round(seconds, 2),
                          "seconds_per_model_turn": round(seconds / max(1, state.turn), 3),
                          "aster_dir_mib": round(stored / 2**20, 1)}))
        if profiler:
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(120)
    finally:
        if args.keep:
            shutil.copytree(root / ".aster", args.keep, dirs_exist_ok=True)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
