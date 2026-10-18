"""Projects whose tests are not a supported framework still get formal, exit-code verification."""

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

pytestmark = pytest.mark.core


def runtime_for(root):
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, sandbox="required"),
                      approval_callback=lambda *_: True)
    runtime.start(TaskSpec(prompt="verify", require_verification=True))
    return runtime


async def run(runtime, command):
    return await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))


@pytest.mark.asyncio
async def test_build_and_test_command_is_judged_by_exit_code_in_a_copy(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\t@mkdir -p build && test -f ok.txt\n")
    runtime = runtime_for(tmp_path)
    failed = await run(runtime, "make test")
    assert failed.reason == "tests_failed" and failed.data["test_results"] == {"command: make test": "failed"}
    assert "exit code" in failed.content and not (tmp_path / "build").exists()  # writes stay in the copy
    (tmp_path / "ok.txt").write_text("x")
    passed = await run(runtime, "make test")
    assert passed.status == "success" and runtime.state.verification.state == "passed"
    assert runtime.state.verification_failures == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["echo ok && true", "true", "ls"])
async def test_commands_that_are_not_tests_stay_in_run_shell(tmp_path, command):
    result = await run(runtime_for(tmp_path), command)
    assert result.reason == "test_command_not_supported"


@pytest.mark.asyncio
async def test_inline_code_still_requires_a_python_framework(tmp_path):
    runtime = runtime_for(tmp_path)
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": "make test", "code": "def test_a(): pass"}))
    assert result.reason == "test_command_not_supported"


@pytest.mark.asyncio
async def test_exit_code_failures_fail_verification_without_a_sticky_obligation(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\t@test -f ok.txt\ncheck:\n\t@test -f ok.txt\n")
    runtime = runtime_for(tmp_path)
    await run(runtime, "make test")
    assert runtime.state.verification.state == "failed" and runtime.state.verification_failures == {}
    assert not runtime.verifier.final(runtime.state).passed
    (tmp_path / "ok.txt").write_text("x")
    passed = await run(runtime, "make check")
    assert passed.status == "success" and runtime.state.verification.state == "passed"


@pytest.mark.asyncio
async def test_files_the_copy_refuses_to_publish_do_not_void_test_evidence(tmp_path):
    (tmp_path / "test_a.py").write_text(
        "import os\n\ndef test_a():\n    os.makedirs('.git', exist_ok=True)\n"
        "    open('.git/index', 'w').write('x')\n")
    runtime = runtime_for(tmp_path)
    result = await run(runtime, "python -m pytest test_a.py")
    assert result.status == "success" and "discarded test-created files" in result.content, result.content
    assert runtime.state.verification.state == "passed" and not (tmp_path / ".git").exists()


@pytest.mark.parametrize("command,accepted", [
    ("make all && make test", True),
    ("cmake -S . -B build && cmake --build build && ctest --test-dir build", True),
    ("g++ -I. a.cpp a_test.cpp -o .tmp/a_tests && .tmp/a_tests", True),
    ("CI=1 npm test", True),
    ("./run_tests.sh", True),
    ("ctest | tail -20", False),
    ("make test || true", False),
    ("make test; true", False),
    ("for f in test/*.scm; do python3 interp.py $f; done", False),
    ("python3 interp.py test/a.scm", False),
    ("test -f ok.txt", False),
])
def test_only_plain_build_and_test_chains_count_by_exit_code(command, accepted):
    from aster.tools.testing import project_test_command
    assert project_test_command(command) is accepted


def test_workspace_is_exposable_only_without_secrets(tmp_path):
    from aster.safety.runner import exposable
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("x = 1\n")
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "fixture.pem").write_text("not a user secret")
    assert exposable(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://user:tok@example.com/r.git\n')
    assert not exposable(tmp_path)
    (tmp_path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://example.com/r.git\n')
    assert exposable(tmp_path)
    (tmp_path / "pkg" / ".env.local").write_text("TOKEN=1")
    assert not exposable(tmp_path)


@pytest.mark.asyncio
async def test_absolute_workspace_paths_resolve_inside_tests(tmp_path):
    # Editable installs and fixtures name the workspace absolutely; tests in a copy must read it.
    (tmp_path / "data.json").write_text('{"v": 1}')
    runtime = runtime_for(tmp_path)
    code = f"import json\ndef test_reads():\n    assert json.load(open({str(tmp_path / 'data.json')!r}))['v'] == 1\n"
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q", "code": code}))
    assert result.status == "success", result.content
    (tmp_path / ".env").write_text("SECRET=1")
    hidden = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "pytest -q", "code": f"def test_secret():\n    open({str(tmp_path / '.env')!r}).read()\n"}))
    assert hidden.status == "error" and "SECRET=1" not in hidden.content


@pytest.mark.asyncio
async def test_metadata_changes_to_the_readable_original_are_restored(tmp_path):
    from aster.safety.runner import SandboxRunner
    from aster.storage.workspace import Workspace
    path = tmp_path / "tool.sh"
    path.write_text("true\n")
    path.chmod(0o644)
    head, tail = str(tmp_path)[:4], str(tmp_path)[4:]  # split so the command text is not remapped
    command = f"python3 -c \"import os; os.chmod({head!r} + {tail!r} + '/tool.sh', 0o777)\""
    result = await SandboxRunner(Settings()).run(Workspace(tmp_path), command)
    assert result.reason == "sandbox_metadata_change" and result.data["restored_modes"] == ["tool.sh"]
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["python -m pytest -q 2>&1 | tail -20", "make test"])
async def test_test_commands_in_run_shell_are_formal_verification(tmp_path, command):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "Makefile").write_text("test:\n\t@python -m pytest -q\n")
    runtime = runtime_for(tmp_path)
    result = await runtime.step(ToolCall(name="run_shell", arguments={"command": command}))
    assert result.status == "success" and "formal verification" in result.content
    assert runtime.state.verification.state == "passed"


@pytest.mark.asyncio
async def test_ordinary_shell_commands_are_not_rerouted(tmp_path):
    (tmp_path / "run.py").write_text("print('hello')\n")
    runtime = runtime_for(tmp_path)
    result = await runtime.step(ToolCall(name="run_shell", arguments={"command": "python run.py"}))
    assert result.status == "success" and "hello" in result.content
    assert runtime.state.verification.state != "passed"


@pytest.mark.asyncio
async def test_home_dotfiles_are_not_task_changes(tmp_path):
    runtime = runtime_for(tmp_path)
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": "mkdir -p ~/.npm/_logs && echo x > ~/.npm/_logs/debug.log && echo ok"}))
    assert result.status == "success" and result.changed_paths == []
    assert not (tmp_path / ".npm").exists()


@pytest.mark.asyncio
async def test_passing_verification_summarises_changes(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 0\n")
    (tmp_path / "test_app.py").write_text("from app import VALUE\n\ndef test_v():\n    assert VALUE == 1\n")
    runtime = runtime_for(tmp_path)
    await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
    await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "app.py", "old_text": "VALUE = 0", "new_text": "VALUE = 1"}))
    result = await run(runtime, "python -m pytest -q")
    assert result.status == "success" and "[Changes since the task began: app.py +1 -1]" in result.content
