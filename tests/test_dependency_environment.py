"""Dependency reuse is isolated from source publication and trusted test runners."""

import asyncio
import importlib.util
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from aster.config import Settings
from aster.runtime import Runtime
from aster.safety.runner import SandboxRunner
from aster.schema import TaskSpec, ToolCall
from aster.workspace import Workspace


def test_model_sees_execution_constraints_and_current_capabilities(tmp_path):
    runtime = Runtime(tmp_path, Settings(network=False, reuse_environment=True))
    runtime.start(TaskSpec(prompt="Build and verify a small website"))
    prompt, _, _ = runtime.prepare_request()
    policy = prompt[0]["content"]
    assert f"Python {sys.version.split()[0]}" in policy
    assert "network=blocked" in policy and "Sandbox=required" in policy
    assert "in this process only" in policy and "background servers do not survive" in policy
    assert "TMPDIR" in policy
    runtime.settings.network = True
    assert "network=allowed" in runtime.prepare_request()[0][0]["content"]


def python(program):
    return shlex.join([sys.executable, "-c", program])


INSTALL_FIXTURE = python(
    "import os; from pathlib import Path; "
    "p=Path(os.environ['PIP_TARGET']); p.mkdir(parents=True, exist_ok=True); "
    "(p/'aster_dependency_probe.py').write_text('VALUE = 42\\n'); "
    "(p/'pytest.py').write_text(\"raise RuntimeError('untrusted pytest shadow')\\n\"); "
    "n=Path('node_modules/probe'); n.mkdir(parents=True, exist_ok=True); "
    "(n/'index.js').write_text('module.exports = 42;'); "
    "print('fixture ready')"
)


async def test_reuse_keeps_dependencies_and_refreshes_only_managed_source(tmp_path):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    (tmp_path / "source.txt").write_text("original")
    result = await runner.run(workspace, INSTALL_FIXTURE)
    assert result.status == "success", result.model_dump()
    assert not (tmp_path / ".venv").exists() and not (tmp_path / "node_modules").exists()
    (tmp_path / "source.txt").write_text("external edit")
    result = await runner.run(
        workspace,
        python(
            "import aster_dependency_probe as p; from pathlib import Path; "
            "assert p.VALUE == 42; assert Path('source.txt').read_text() == 'external edit'; "
            "Path('created.txt').write_text('source artifact')"
        ),
    )
    assert result.status == "success", result.model_dump()
    assert result.changed_paths == ["created.txt"]
    assert (tmp_path / "created.txt").read_text() == "source artifact"
    assert runner.environment_generation == 2
    (tmp_path / "source.txt").unlink()
    result = await runner.run(workspace, python("from pathlib import Path; assert not Path('source.txt').exists()"))
    assert result.status == "success"
    root = Path(runner._environment.name)
    runner.close()
    assert not root.exists() and runner.environment_generation == 4


async def test_default_environment_does_not_reuse_dependencies(tmp_path):
    runner = SandboxRunner(Settings())
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, "mkdir -p node_modules/probe")).status == "success"
    result = await runner.run(workspace, "test -d node_modules/probe")
    assert result.status == "error" and runner.environment_generation == 0


@pytest.mark.parametrize("sandbox", ["off", "best_effort"])
async def test_reuse_never_degrades_to_the_real_workspace(tmp_path, sandbox, monkeypatch):
    with pytest.raises(ValidationError, match="requires sandbox=required"):
        Settings(reuse_environment=True, sandbox=sandbox)
    # Internal model_copy updates bypass Pydantic; the execution boundary also rejects them.
    settings = Settings(reuse_environment=True).model_copy(update={"sandbox": sandbox})
    runner = SandboxRunner(settings)

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("unsafe fallback executed")

    monkeypatch.setattr(runner, "_process", must_not_run)
    result = await runner.run(Workspace(tmp_path), INSTALL_FIXTURE)
    assert result.status == "blocked" and result.reason == "isolation_required_for_environment"
    assert not (tmp_path / ".venv").exists() and runner._environment is None


async def test_readonly_and_scoped_commands_borrow_dependencies_without_writes(tmp_path):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, INSTALL_FIXTURE)).status == "success"
    generation = runner.environment_generation
    check = python("import aster_dependency_probe as p; assert p.VALUE == 42")
    assert (await runner.run(workspace, check, readonly=True)).status == "success"
    result = await runner.run(
        workspace,
        python("import aster_dependency_probe as p; from pathlib import Path; Path(p.__file__).write_text('changed')"),
        readonly=True,
    )
    assert result.status == "error" and "PermissionError" in result.content
    assert runner.environment_generation == generation
    runner.settings = runner.settings.model_copy(update={"write_scope": ["allowed.txt"]})
    result = await runner.run(workspace, check + " && printf wrong > forbidden.txt")
    assert result.status == "blocked" and result.reason == "write_scope"
    assert not (tmp_path / "forbidden.txt").exists()
    assert runner.environment_generation == generation
    runner.close()


@pytest.mark.parametrize("operation", ["os.chmod(p.__file__, 0o600)", "os.utime(p.__file__, (0, 0))"])
async def test_readonly_borrow_cannot_change_dependency_metadata(tmp_path, operation):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, INSTALL_FIXTURE)).status == "success"
    dependency = next(Path(runner._environment.name).rglob("aster_dependency_probe.py"))
    before = dependency.stat()
    result = await runner.run(
        workspace, python("import os, aster_dependency_probe as p; " + operation), readonly=True
    )
    assert result.status == "error" and "PermissionError" in result.content
    after = dependency.stat()
    assert (before.st_mode, before.st_mtime_ns) == (after.st_mode, after.st_mtime_ns)
    runner.close()


@pytest.mark.parametrize(
    "command,reason",
    [
        ("ln -s /etc node_modules/escape", "dependency_symlink_escape"),
        ("ln -s missing node_modules/broken", "invalid_dependency_symlink"),
        ("mkfifo node_modules/pipe", "dependency_special_file"),
        ("mkdir .aster; printf forged > .aster/checkpoint.json", "sandbox_protected_output"),
    ],
)
async def test_dependency_escape_or_protected_output_discards_environment(tmp_path, command, reason):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, INSTALL_FIXTURE)).status == "success"
    old_root = Path(runner._environment.name)
    result = await runner.run(workspace, "printf 'captured evidence'; printf unpublished > source.txt; " + command)
    assert result.status == "blocked" and result.reason == reason, result.model_dump()
    assert result.content == "captured evidence"
    assert result.data["environment_generation"] == runner.environment_generation
    assert not old_root.exists() and runner._environment is None
    assert not (tmp_path / "source.txt").exists() and not (tmp_path / ".aster").exists()


async def test_venv_default_symlinks_and_node_bin_links_stay_inside_owned_environment(tmp_path):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    result = await runner.run(
        workspace,
        shlex.join([sys.executable, "-m", "venv", "--without-pip", ".venv"])
        + " && mkdir -p node_modules/.bin node_modules/probe "
        + "&& printf usable > node_modules/probe/bin.txt "
        + "&& ln -s ../probe/bin.txt node_modules/.bin/probe",
    )
    assert result.status == "success", result.model_dump()
    result = await runner.run(workspace, "cat node_modules/.bin/probe")
    assert result.status == "success" and result.content == "usable"
    assert not (tmp_path / ".venv").exists()
    runner.close()


async def test_runners_never_share_writable_dependencies(tmp_path):
    workspace = Workspace(tmp_path)
    first = SandboxRunner(Settings(reuse_environment=True))
    second = SandboxRunner(Settings(reuse_environment=True))
    assert (await first.run(workspace, INSTALL_FIXTURE)).status == "success"
    result = await second.run(workspace, python("import aster_dependency_probe"))
    assert result.status == "error" and "ModuleNotFoundError" in result.content
    assert (await first.run(workspace, python("import aster_dependency_probe"))).status == "success"
    first.close()
    second.close()


async def test_cancellation_discards_environment_and_releases_runner(tmp_path, monkeypatch):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, INSTALL_FIXTURE)).status == "success"
    old_root = Path(runner._environment.name)
    entered = asyncio.Event()
    original = runner._process

    async def slow_process(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(runner, "_process", slow_process)
    task = asyncio.create_task(runner.run(workspace, "sleep 20"))
    await entered.wait()
    with pytest.raises(RuntimeError, match="execution_environment_busy"):
        await runner.run(workspace, "true")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not old_root.exists() and runner._environment is None
    monkeypatch.setattr(runner, "_process", original)
    assert (await runner.run(workspace, "true")).status == "success"
    runner.close()


async def test_cancelling_a_live_sandbox_process_removes_its_execution_root(tmp_path):
    runner = SandboxRunner(Settings(reuse_environment=True))
    workspace = Workspace(tmp_path)
    assert (await runner.run(workspace, INSTALL_FIXTURE)).status == "success"
    root = Path(runner._environment.name)
    task = asyncio.create_task(runner.run(workspace, python(
        "import time; from pathlib import Path; Path('.tmp/ready').write_text('started'); time.sleep(30)"
    )))

    async def started():
        while not (root / ".tmp/ready").exists():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(started(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not root.exists() and runner._environment is None
    assert not runner._busy


@pytest.mark.parametrize("framework", ["pytest", "unittest", "node"])
async def test_formal_tests_borrow_same_dependencies_without_mutating_them(tmp_path, framework):
    if framework == "node" and shutil.which("node") is None:
        pytest.skip("Node is unavailable")
    runtime = Runtime(
        tmp_path, Settings(reuse_environment=True, approval="auto", memory_enabled=False)
    )
    runtime.start(TaskSpec(prompt="Inspect the dependency"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={"command": INSTALL_FIXTURE}))
    assert result.status == "success", result.model_dump()
    if framework == "node":
        (tmp_path / "example.test.cjs").write_text(
            "const test=require('node:test'); const assert=require('node:assert/strict'); "
            "test('dependency',()=>{assert.equal(require('probe'),42); "
            "assert.throws(()=>require('node:fs').writeFileSync(require.resolve('probe'),'changed'));});"
        )
        command = "node --test example.test.cjs"
    else:
        (tmp_path / "test_probe.py").write_text(
            "import unittest\nfrom pathlib import Path\nimport aster_dependency_probe as p\n"
            "class DependencyTest(unittest.TestCase):\n"
            "    def test_dependency(self):\n"
            "        self.assertEqual(p.VALUE, 42)\n"
            "        with self.assertRaises(PermissionError):\n"
            "            Path(p.__file__).write_text('changed')\n"
        )
        command = "pytest -q" if framework == "pytest" else "python -m unittest test_probe"
    generation = runtime.sandbox.environment_generation
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))
    assert result.status == "success", result.model_dump()
    assert result.data["tested_environment_generation"] == generation
    assert runtime.sandbox.environment_generation == generation
    assert runtime.state.verification.state == "passed"
    runtime.sandbox.close()


async def test_offline_pip_target_survives_separate_commands_and_formal_tests(tmp_path):
    module = "aster_execution_reuse_probe"
    assert importlib.util.find_spec(module) is None
    (tmp_path / "test_installed.py").write_text(
        f"import {module} as dependency\n"
        "def test_installed_dependency():\n"
        "    assert dependency.VALUE == 'local wheel'\n"
    )
    runtime = Runtime(
        tmp_path, Settings(reuse_environment=True, approval="auto", memory_enabled=False),
        approval_callback=lambda *_: True,
    )
    runtime.start(TaskSpec(prompt="Verify the installed local package"))
    pyproject = (
        '[build-system]\nrequires=["setuptools", "wheel"]\n'
        'build-backend="setuptools.build_meta"\n'
        '[project]\nname="aster-execution-reuse-probe"\nversion="0.0.1"\n'
        f'[tool.setuptools]\npy-modules=["{module}"]\n'
    )
    create = python(
        "from pathlib import Path; p=Path('.tmp/pkg'); p.mkdir(parents=True); "
        f"(p/'pyproject.toml').write_text({pyproject!r}); "
        f"(p/{(module + '.py')!r}).write_text(\"VALUE = 'local wheel'\\n\")"
    )
    install = shlex.join([
        sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
        "--no-build-isolation", "--no-cache-dir", "--disable-pip-version-check", ".tmp/pkg",
    ])
    result = await runtime.step(
        ToolCall(name="run_shell", arguments={"command": create + " && " + install, "timeout": 60})
    )
    assert result.status == "success", result.model_dump()
    assert "Successfully installed aster-execution-reuse-probe-0.0.1" in result.content
    result = await runtime.step(ToolCall(name="run_shell", arguments={"command": python(
        f"import {module} as p; import os; from pathlib import Path; "
        "assert p.VALUE == 'local wheel'; "
        "assert Path(p.__file__).parent == Path(os.environ['PIP_TARGET']); "
        "print('installed target reused')"
    )}))
    assert result.status == "success" and result.content == "installed target reused\n"
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q"}))
    assert result.status == "success" and result.data["passed_tests"] == 1, result.model_dump()
    assert not (tmp_path / ".venv").exists() and importlib.util.find_spec(module) is None
    runtime.sandbox.close()
