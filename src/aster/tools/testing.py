"""Isolated formal test execution and structured outcomes for verification."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..safety.policy import local_python_paths
from ..safety.runner import SandboxRunner, map_root
from ..schema import Outcome
from ..storage.workspace import Workspace

TEST_COMMAND_PREFIXES = [
    ["pytest"],
    ["python", "-m", "pytest"],
    ["python", "-m", "unittest"],
    ["python"],  # Workspace test scripts; run_tests validates the entry point.
    ["node", "--test"],
    ["go", "test"],
]


# A failure is attributed to missing network access only when the error is an unreachable
# network or failed name resolution AND the traceback shows a real socket call. Refused
# connections stay ordinary failures: a local service the task must start may be down.
_NETWORK_ERROR = re.compile(
    r"\[Errno (?:101|113|-2|-3|-5)\]|Network is unreachable|No route to host"
    r"|Temporary failure in name resolution|Name or service not known"
    r"|nodename nor servname provided|getaddrinfo failed|NameResolutionError"
)
_SOCKET_FRAME = re.compile(r"sock\.connect\(|getaddrinfo\(|create_connection\(|/socket\.py:")


# Aster's own no-network sandbox rejects socket creation instead of routing.
_SOCKET_DENIED = re.compile(r"_socket\.socket\.__init__\(.*\n(?:.*\n){0,3}?E\s+PermissionError: \[Errno 1\]")


def network_unavailable(detail: str) -> bool:
    return bool(_NETWORK_ERROR.search(detail) and _SOCKET_FRAME.search(detail)) or bool(
        _SOCKET_DENIED.search(detail)
    )


def jest_entry(argv: list[str]) -> int | None:
    """Length of a Jest entry point (jest, npx jest, node <...>/jest/bin/jest.js), else None."""
    if argv[:1] == ["jest"]:
        return 1
    if argv[:2] == ["npx", "jest"]:
        return 2
    if (len(argv) > 1 and Path(argv[0]).name == "node"
            and Path(argv[1]).name in {"jest", "jest.js"} and "jest" in Path(argv[1]).parts[:-1]):
        return 2
    return None


def parse_jest(text: str, root: Path | None = None) -> list[dict[str, str]]:
    """Convert Jest's built-in --json report into the shared case records."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("testResults"), list):
        raise ValueError("invalid_jest_report")
    cases = []
    for suite in data["testResults"]:
        name = Path(suite.get("name", ""))
        try:
            name = name.relative_to(root) if root is not None else name
        except ValueError:
            pass
        assertions = suite.get("assertionResults") or []
        if not assertions and suite.get("status") == "failed":
            # The file failed before declaring tests (syntax error, missing module).
            cases.append({"name": str(name), "classname": str(name), "status": "error",
                          "detail": (suite.get("message") or "")[-20000:]})
        for case in assertions:
            status = {"passed": "passed", "failed": "failed"}.get(case.get("status"), "skipped")
            title = case.get("fullName") or " ".join([*case.get("ancestorTitles", []), case.get("title", "")])
            cases.append({"name": title.strip(), "classname": str(name), "status": status,
                          "detail": "\n".join(case.get("failureMessages") or [])[-20000:]})
    return cases


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
        detail = ""
        for child in case:
            if child.tag in {"failure", "error"}:
                detail += (child.get("message") or "") + "\n" + (child.text or "")
        cases.append(
            {"name": case.get("name"), "classname": case.get("classname", ""), "status": status,
             "detail": detail[-20000:]}
        )
    return cases


async def run_tests(
    workspace: Workspace,
    sandbox: SandboxRunner,
    command: str,
    timeout: int,
    *,
    runner: type[SandboxRunner] = SandboxRunner,
    code: str = "",
) -> Outcome:
    """Run tests in a disposable copy; publish only structured, version-bound evidence."""
    # Tests run in a copy, so absolute paths into the real workspace become relative ones.
    command = map_root(command, workspace.root, Path("."))
    # Tests already start in the workspace copy, so a leading `cd <workspace> &&` is a no-op.
    if leading := re.match(r"\s*cd\s+\.?/?\s*&&\s*(.+)$", command, re.S):
        command = leading.group(1)
    argv = shlex.split(command)
    jest = jest_entry(argv)
    verification_command = shlex.join(argv)
    python_paths = []
    if len(argv) > 1 and argv[1] == "python" and (paths := local_python_paths(argv[0])):
        python_paths, argv = paths, argv[1:]
    native = len(argv) > 1 and argv[0] == "python" and not argv[1].startswith("-")
    if (jest is None and not any(argv[: len(p)] == p for p in TEST_COMMAND_PREFIXES)) or (
        argv[0] == "python" and not native
        and argv[1:3] not in [["-m", "pytest"], ["-m", "unittest"]]
    ) or any(
        c in command for c in ";|&><$`\n"
    ):
        return Outcome(
            status="blocked",
            reason="test_command_not_supported",
            content=(
                "Use pytest, python -m pytest/unittest, python <workspace test script>, node --test, jest, or go test without a .venv/bin/ "
                "path. Python accepts a PYTHONPATH prefix containing only workspace-relative directories. "
                "With reuse_environment enabled, installed "
                "project .venv dependencies are exposed automatically; keep them installed until "
                "verification finishes. Other commands belong in run_shell, which does not "
                "update formal test evidence."
            ),
        )
    if native:
        try:
            if Path(argv[1]).is_absolute() or not workspace.path(argv[1]).is_file():
                raise ValueError("test_script_not_in_workspace")
        except (OSError, ValueError) as exc:
            return Outcome(status="blocked", reason="test_script_not_in_workspace", content=str(exc))
    if sandbox.settings.sandbox == "off":
        return Outcome(status="blocked", reason="isolation_required_for_scope")
    unittest = argv[:3] == ["python", "-m", "unittest"]
    node = argv[:2] == ["node", "--test"]
    go = argv[:2] == ["go", "test"]
    options = (argv[jest:] if jest else argv[2:] if native or node or go
               else argv[3:] if argv[0] == "python" else argv[1:])
    if code and (jest or native or node or go or (unittest and "discover" in options)):
        return Outcome(status="blocked", reason="inline_test_command_not_supported",
                       content="With code, use pytest, python -m pytest, or python -m unittest without discover.")
    if node and any(option.startswith("--test-reporter") for option in options):
        return Outcome(
            status="blocked", reason="test_reporter_reserved",
            content="Remove --test-reporter and --test-reporter-destination; run_tests supplies its own structured reporter. Retry with node --test and your test paths.",
        )
    if jest and any(option.split("=")[0] in {"--json", "--outputFile", "--reporters"} for option in options):
        return Outcome(
            status="blocked", reason="test_reporter_reserved",
            content="Remove --json, --outputFile and --reporters; run_tests captures Jest's structured results automatically.",
        )
    if go and any(option == "-json" or option.startswith("-json=") for option in options):
        return Outcome(
            status="blocked", reason="test_reporter_reserved",
            content="Remove -json; run_tests captures Go's structured test events automatically.",
        )
    tested_fingerprint = workspace.fingerprint()
    tested_environment = sandbox.environment_generation
    with tempfile.TemporaryDirectory(prefix="aster-tests-") as directory:
        target = Path(directory)
        for source in workspace.files():
            copied = target / source.relative_to(workspace.root)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
        if code:
            # Temporary tests share the existing sandbox/report path, never the user's files.
            inline_name = "__aster_inline_" + target.name.replace("-", "_") + ".py"
            (target / inline_name).write_text(code, encoding="utf-8")
            options.append(inline_name)
        report = "__aster_result_" + target.name + (".json" if jest else ".xml")
        if jest:
            command = [*argv[:jest], *options, "--json", f"--outputFile={report}"]
        elif node:
            command = [
                "node",
                "--test",
                "--test-reporter=junit",
                f"--test-reporter-destination={report}",
                *options,
            ]
        elif go:
            # Convert Go's test events, not human-readable PASS lines, into our shared report.
            program = """import json, subprocess, sys, xml.etree.ElementTree as ET
report = sys.argv[1]
try:
    process = subprocess.Popen(['go', 'test', '-json', *sys.argv[2:]], stdout=subprocess.PIPE, text=True)
except FileNotFoundError:
    raise SystemExit(127)
suite = ET.Element('testsuite')
failed_packages = set()
for line in process.stdout:
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        case = ET.SubElement(suite, 'testcase', name='invalid test event', classname='go')
        ET.SubElement(case, 'error').text = line
        continue
    if event.get('Output'):
        print(event['Output'], end='', flush=True)
    action, name, package = event.get('Action'), event.get('Test'), event.get('Package', '')
    if name and action in {'pass', 'fail', 'skip'}:
        case = ET.SubElement(suite, 'testcase', name=name, classname=package)
        if action != 'pass':
            ET.SubElement(case, 'failure' if action == 'fail' else 'skipped')
        if action == 'fail':
            failed_packages.add(package)
    elif not name and action == 'fail' and package not in failed_packages:
        case = ET.SubElement(suite, 'testcase', name=package or 'package startup', classname=package)
        ET.SubElement(case, 'error')
ET.ElementTree(suite).write(report, encoding='unicode')
raise SystemExit(process.wait())
"""
            command = ["python", "-I", "-c", program, report, *options]
        elif unittest or native:
            # Import trusted frameworks before exposing repository import roots.
            program = """import os, runpy, sys, unittest, xml.etree.ElementTree as ET
report = sys.argv.pop(1)
sys.path.insert(0, os.getcwd())
suite = ET.Element('testsuite')
observed = False
class StructuredResult(unittest.TextTestResult):
    def startTestRun(self):
        self.successes = {}
    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes[test.id()] = f'{type(test).__module__}.{type(test).__qualname__}'
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is None:
            self.successes[subtest.id()] = f'{type(subtest).__module__}.{type(subtest).__qualname__}'
class Runner(unittest.TextTestRunner):
    resultclass = StructuredResult
def record_unittest(result):
    global observed
    observed = True
    if not isinstance(result, StructuredResult):
        ET.SubElement(ET.SubElement(suite, 'testcase', name='untracked unittest result', classname='unittest'), 'error')
        return
    outcomes = [('failure', result.failures), ('error', result.errors),
                ('skipped', result.skipped), ('skipped', result.expectedFailures),
                ('failure', [(test, 'unexpected success') for test in result.unexpectedSuccesses])]
    for status, entries in outcomes:
        for test, detail in entries:
            result.successes.pop(test.id(), None)
            if status == 'skipped':
                # An expected failure cannot leave earlier subtests counted as success.
                for name in list(result.successes):
                    if name.startswith(test.id() + ' ('):
                        result.successes.pop(name)
            case = ET.SubElement(suite, 'testcase', name=test.id(),
                                 classname=f'{type(test).__module__}.{type(test).__qualname__}')
            ET.SubElement(case, status).text = detail
    for name, classname in sorted(result.successes.items()):
        ET.SubElement(suite, 'testcase', name=name, classname=classname)
"""
            if native:
                program = program.replace(
                    "sys.path.insert(0, os.getcwd())",
                    "try:\n    import pytest\nexcept ImportError:\n    pytest = None\n"
                    "sys.path.insert(0, os.getcwd())",
                )
                program += """
original_run = unittest.TextTestRunner.run
def run_unittest(self, *args, **kwargs):
    result = original_run(self, *args, **kwargs)
    record_unittest(result)
    return result
unittest.TextTestResult = StructuredResult
unittest.TextTestRunner.resultclass = StructuredResult
unittest.TextTestRunner.run = run_unittest
if pytest is not None:
    original_pytest = pytest.main
    def run_pytest(args=None, plugins=None):
        global observed
        args = list(sys.argv[1:] if args is None else args)
        path = os.path.join('.tmp', 'native-pytest.xml')
        if os.path.exists(path):
            os.unlink(path)
        boundary = args.index('--') if '--' in args else len(args)
        args[boundary:boundary] = [f'--junitxml={path}', '-p', 'no:cacheprovider']
        code = original_pytest(args, plugins=plugins)
        try:
            cases = list(ET.parse(path).getroot().iter('testcase'))
        except (OSError, ET.ParseError):
            cases, code = [], code or 1
        suite.extend(cases)
        if code and not any(case.find('failure') is not None or case.find('error') is not None for case in cases):
            ET.SubElement(ET.SubElement(suite, 'testcase', name='pytest runner', classname='pytest'), 'error')
        observed = True
        return code
    pytest.main = run_pytest
entry = sys.argv.pop(1)
sys.argv[0] = entry
sys.path.insert(0, os.path.dirname(os.path.abspath(entry)))
if os.path.normpath(entry) == 'bin/test' and os.path.isfile('sympy/utilities/runtests.py'):
    import builtins
    original_import = builtins.__import__
    def import_sympy(name, *args, **kwargs):
        module = original_import(name, *args, **kwargs)
        if name == 'sympy' and not getattr(module.__spec__, '_initializing', False):
            builtins.__import__ = original_import
            sympy = module
            from sympy.utilities.runtests import PyTestReporter
            original_finish = PyTestReporter.finish
            def finish(self):
                global observed
                value = original_finish(self)
                counts = [(self._passed, None), (self._skipped + self._xfailed, 'skipped'),
                          (len(self._failed) + len(self._failed_doctest) + len(self._xpassed), 'failure'),
                          (len(self._exceptions), 'error')]
                for count, status in counts:
                    for index in range(count):
                        case = ET.SubElement(suite, 'testcase', name=f'{status or "passed"}-{index}', classname='sympy')
                        if status:
                            ET.SubElement(case, status)
                observed = True
                return value
            PyTestReporter.finish = finish
            original_test = sympy.test
            def test(*args, **kwargs):
                kwargs['subprocess'] = False  # Keep reporter hooks in this sandboxed process.
                return original_test(*args, **kwargs)
            sympy.test = test
        return module
    # Let bin/test apply -C/ground-type options before importing its framework.
    builtins.__import__ = import_sympy
try:
    runpy.run_path(entry, run_name='__main__')
    code = 0
except SystemExit as exc:
    code = exc.code
if observed:
    ET.ElementTree(suite).write(report, encoding='unicode')
raise SystemExit(code)
"""
                command = ["python", "-I", "-c", program, report, argv[1], *options]
            else:
                program += """
result = unittest.main(module=None, argv=['unittest', *sys.argv[1:]],
                       testRunner=Runner, exit=False).result
record_unittest(result)
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
        if not node and not go and not jest and python_paths:
            marker = "sys.path.insert(0, os.getcwd())"
            program = program.replace(marker, marker + f"\nsys.path[:0] = {python_paths!r}")
            command[3] = program
        if not node and not go and not jest and sandbox.settings.reuse_environment:
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
        report_text = staged_root = None

        class ReportRunner(runner):
            def _publish(self, workspace, staged, before, after, result):
                nonlocal report_text, staged_root
                staged_root = Path(staged)
                try:
                    report_text = Workspace(staged).read(report)
                except (OSError, ValueError, UnicodeError):
                    pass
                return super()._publish(workspace, staged, before, after, result)

        # Only a report may leave the execution copy, into this disposable copy.
        # Tests cannot publish any change to the actual repository, including in plan mode.
        # Capture it before publication can reject an unrelated test-created file.
        result = await ReportRunner(settings, environment=sandbox).run(
            Workspace(target), shlex.join(command), timeout
        )
        cases = None
        if native:
            result.data["test_evidence_source"] = "native_framework"
            if Path(argv[1]).as_posix() in {"bin/test", "./bin/test"} and workspace.path("sympy/utilities/runtests.py").is_file():
                result.data["native_sympy_subprocess"] = False
                result.content = (
                    "[Aster captures SymPy's native reporter in the current isolated process; "
                    "its extra hash-randomization subprocess is disabled.]\n" + result.content
                )
        try:
            text = report_text if report_text is not None else Workspace(target).read(report)
            cases = parse_jest(text, staged_root) if jest else parse_junit(text)
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
        if not native and not unittest and not node and not go and not jest and result.exit_code == 5:
            result.reason = "no_tests_collected"
        elif not native and not unittest and not node and not go and not jest and result.exit_code == 2:
            result.reason = (
                "test_collection_failed"
                if any(case["status"] == "error" for case in cases or [])
                else "test_interrupted"
            )
        elif not native and not unittest and not node and not go and not jest and result.exit_code in {3, 4}:
            result.reason = "test_runner_error"
        elif result.exit_code == 127:
            result.reason = "test_runner_unavailable"
        else:
            result.reason = failure_reason
    result.data["passed_tests"] = passed if result.status == "success" else 0
    # Preserve case identities for the verifier; these stay out of model tool output.
    test_results = {}
    if node or result.data.get("native_sympy_subprocess") is False:
        # ponytail: anonymous reports use the same command as their scope;
        # support narrower reruns when these reporters expose stable case identities.
        key = "command: " + verification_command
        if any(case["status"] == "failed" for case in cases or []):
            test_results[key] = "failed"
        elif result.status == "success":
            test_results[key] = "passed"
    else:
        scope = (argv[1] if native else "go" if go else "jest" if jest
                 else "unittest" if unittest else "pytest")
        for case in cases or []:
            status = case["status"]
            if status == "error" and unittest and case["classname"] != "unittest.loader._FailedTest":
                status = "failed"
            if status not in {"passed", "failed"}:
                continue
            if status == "passed" and result.status not in {"success", "error"}:
                continue  # An interrupted or blocked run cannot clear a known failure.
            classname, name = case["classname"], case["name"]
            if code:
                # Each inline execution has a fresh filename, but the same test identity.
                classname = classname.replace(Path(inline_name).stem, "__aster_inline__")
                name = name.replace(Path(inline_name).stem, "__aster_inline__")
            key = f"{scope}: {classname}::{name}"
            if status == "failed" and network_unavailable(case.get("detail", "")):
                status = "network_unavailable"
            if test_results.get(key) != "failed":
                test_results[key] = status
    result.data["test_results"] = test_results
    unreachable = sorted(key for key, status in test_results.items() if status == "network_unavailable")
    if unreachable:
        shown = ", ".join(unreachable[:10]) + (f" (+{len(unreachable) - 10} more)" if len(unreachable) > 10 else "")
        result.content += (
            f"\n[Aster: {len(unreachable)} failing test(s) failed only because the network is unreachable "
            f"from this sandbox: {shown}. They cannot pass here, so they do not block completion; "
            "do not edit code to work around the missing network. List them as unverified in your final answer.]"
        )
        if not any(status == "failed" for status in test_results.values()) and result.reason == "tests_failed":
            result.data["network_only_failures"] = True
    return result
