"""Isolated formal test execution and structured outcomes for verification."""

from __future__ import annotations

import shlex
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..safety.runner import SandboxRunner
from ..schema import Outcome
from ..workspace import Workspace

TEST_COMMAND_PREFIXES = [
    ["pytest"],
    ["python", "-m", "pytest"],
    ["python", "-m", "unittest"],
    ["node", "--test"],
]


def parse_junit(text: str) -> list[dict[str, str]]:
    root = ET.fromstring(text)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("invalid_junit_report")
    cases = []
    for case in root.iter("testcase"):
        if not case.get("name"):
            raise ValueError("unnamed_junit_test")
        children = {child.tag for child in case}
        status = next(
            (
                result
                for tag, result in (
                    ("error", "error"),
                    ("failure", "failed"),
                    ("skipped", "skipped"),
                )
                if tag in children
            ),
            "passed",
        )
        cases.append(
            {"name": case.get("name"), "classname": case.get("classname", ""), "status": status}
        )
    return cases


async def run_tests(
    workspace: Workspace,
    sandbox: SandboxRunner,
    command: str,
    timeout: int,
    *,
    runner: type[SandboxRunner] = SandboxRunner,
) -> Outcome:
    """Run tests in a disposable copy; publish only structured, version-bound evidence."""
    argv = shlex.split(command)
    if not any(argv[: len(p)] == p for p in TEST_COMMAND_PREFIXES) or any(
        c in command for c in ";|&><$`\n"
    ):
        return Outcome(
            status="blocked",
            reason="test_command_not_supported",
            content=(
                "Use pytest, python -m pytest/unittest, or node --test without a .venv/bin/ "
                "path or environment-variable prefix. With reuse_environment enabled, installed "
                "project .venv dependencies are exposed automatically; keep them installed until "
                "verification finishes. Other commands belong in run_shell, which does not "
                "update formal test evidence."
            ),
        )
    if sandbox.settings.sandbox == "off":
        return Outcome(status="blocked", reason="isolation_required_for_scope")
    unittest = argv[:3] == ["python", "-m", "unittest"]
    node = argv[:2] == ["node", "--test"]
    options = argv[2:] if node else argv[3:] if argv[0] == "python" else argv[1:]
    if node and any(option.startswith("--test-reporter") for option in options):
        return Outcome(
            status="blocked", reason="test_reporter_reserved",
            content="Remove --test-reporter and --test-reporter-destination; run_tests supplies its own structured reporter. Retry with node --test and your test paths.",
        )
    tested_fingerprint = workspace.fingerprint()
    tested_environment = sandbox.environment_generation
    with tempfile.TemporaryDirectory(prefix="aster-tests-") as directory:
        target = Path(directory)
        for source in workspace.files():
            copied = target / source.relative_to(workspace.root)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
        report = "__aster_result_" + target.name + ".xml"
        if node:
            command = [
                "node",
                "--test",
                "--test-reporter=junit",
                f"--test-reporter-destination={report}",
                *options,
            ]
        elif unittest:
            # Import the standard runner before adding repository modules to sys.path.
            # TextTestResult already owns failures/skips; no stdout parsing is needed.
            program = """import os, sys, unittest, xml.etree.ElementTree as ET
report = sys.argv.pop(1)
sys.path.insert(0, os.getcwd())
class StructuredResult(unittest.TextTestResult):
    def startTestRun(self):
        self.successes = set()
    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.add(test.id())
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is None:
            self.successes.add(test.id())
class Runner(unittest.TextTestRunner):
    resultclass = StructuredResult
result = unittest.main(module=None, argv=['unittest', *sys.argv[1:]],
                       testRunner=Runner, exit=False).result
suite = ET.Element('testsuite')
outcomes = [('failure', result.failures), ('error', result.errors),
            ('skipped', result.skipped), ('skipped', result.expectedFailures),
            ('failure', [(test, 'unexpected success') for test in result.unexpectedSuccesses])]
for status, entries in outcomes:
    for test, detail in entries:
        result.successes.discard(test.id())
        case = ET.SubElement(suite, 'testcase', name=test.id(),
                             classname=f'{type(test).__module__}.{type(test).__qualname__}')
        ET.SubElement(case, status).text = detail
for name in sorted(result.successes):
    ET.SubElement(suite, 'testcase', name=name, classname='unittest')
ET.ElementTree(suite).write(report, encoding='unicode')
raise SystemExit(not result.wasSuccessful())
"""
            command = ["python", "-I", "-c", program, report, *options]
        else:
            # Keep repository import semantics while importing the installed pytest first.
            program = """import os, sys
try:
    import pytest
except ImportError:
    raise SystemExit(127)
sys.path.insert(0, os.getcwd())
raise SystemExit(pytest.main(sys.argv[1:]))
"""
            boundary = options.index("--") if "--" in options else len(options)
            options[boundary:boundary] = [f"--junitxml={report}", "-p", "no:cacheprovider"]
            command = ["python", "-I", "-c", program, *options]
        if not node and sandbox.settings.reuse_environment:
            # -I intentionally ignores PYTHONPATH. Import the trusted test runner
            # first, then expose only this sandbox's controlled dependency target.
            marker = "sys.path.insert(0, os.getcwd())"
            program = program.replace(
                marker,
                marker
                + "\n"
                + (
                    "sys.path.insert(1, os.path.join(os.getcwd(), '.venv', 'lib', "
                    "f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages'))"
                ),
            )
            command[3] = program
        settings = sandbox.settings.model_copy(update={"read_only": False, "write_scope": [report]})
        # Only a report may leave the execution copy, into this disposable copy.
        # Tests cannot publish any change to the actual repository, including in plan mode.
        result = await runner(settings, environment=sandbox).run(
            Workspace(target), shlex.join(command), timeout
        )
        cases = None
        try:
            cases = parse_junit(Workspace(target).read(report))
            if node:
                # Node treats an empty test file itself as a passing case. Require
                # declared node:test cases; a file's failed startup remains a failure.
                cases = [
                    case
                    for case in cases
                    if not (case["status"] == "passed" and Path(case["name"]).is_absolute())
                ]
        except (OSError, ValueError, ET.ParseError):
            pass
        result.changed_paths = []
        result.partial_success = False
        result.data["tested_workspace_hash"] = tested_fingerprint
        result.data["tested_environment_generation"] = tested_environment
    if result.status == "success" and (
        workspace.fingerprint() != tested_fingerprint
        or sandbox.environment_generation != tested_environment
    ):
        result.status, result.reason = "blocked", "workspace_changed_during_verification"
    if result.status in {"blocked", "timeout", "cancelled"}:
        if result.reason == "write_scope":
            result.reason = "read_only"
        if not result.reason:
            result.reason = f"test_{result.status}"
        return result
    passed = sum(case["status"] == "passed" for case in cases or [])
    failures = any(case["status"] in {"failed", "error"} for case in cases or [])
    errors = [case for case in cases or [] if case["status"] == "error"]
    # Framework errors do not establish that a recalled lesson caused a bad answer.
    failure_reason = "tests_failed" if failures else "test_runner_error"
    if errors:
        failure_reason = "test_execution_error" if unittest else "test_setup_or_teardown_failed"
        if any(case["classname"] == "unittest.loader._FailedTest" for case in errors):
            failure_reason = "test_collection_failed"
    if result.status == "success":
        if cases is None:
            result.status = "blocked"
            result.reason = (
                "no_tests_collected"
                if any(option in {"-h", "--help"} for option in options)
                else "test_report_missing_or_invalid"
            )
        elif failures:
            result.status, result.reason = "error", failure_reason
        elif not passed:
            result.status = "blocked"
            result.reason = "no_tests_executed" if cases else "no_tests_collected"
    elif result.status == "error":
        if not unittest and not node and result.exit_code == 5:
            result.reason = "no_tests_collected"
        elif not unittest and not node and result.exit_code == 2:
            result.reason = (
                "test_collection_failed"
                if any(case["status"] == "error" for case in cases or [])
                else "test_interrupted"
            )
        elif not unittest and not node and result.exit_code in {3, 4}:
            result.reason = "test_runner_error"
        elif result.exit_code == 127:
            result.reason = "test_runner_unavailable"
        else:
            result.reason = failure_reason
    result.data["passed_tests"] = passed if result.status == "success" else 0
    return result
