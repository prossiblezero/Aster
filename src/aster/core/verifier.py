"""Task and workspace verification; task success never comes from loop termination."""

from __future__ import annotations

import re

from ..schema import Outcome, RunState, Verification, VerifierResult
from ..storage.workspace import Workspace

# Task requirements are guessed from the request with keywords, not with a model. Wrongly
# demanding an edit is the costly mistake: the completion check would push the agent to change
# code nobody asked it to change. So English keywords must be whole words ("dispatch" is not
# "patch"), a negated verb never counts as an edit request, and a negation makes the task
# read-only only when it targets the workspace itself ("do not modify files", "不要修改代码"),
# not a property of the solution ("without changing existing values", "不要修改原始数据").
_EDIT_EN = re.compile(
    r"\b(?:re)?(?:fix(?:es|ing)?|implement(?:s|ing)?|writ(?:e|es|ing)|edit(?:s|ing)?|"
    r"modif(?:y|ies|ying)|creat(?:e|es|ing)|patch(?:es|ing)?|updat(?:e|es|ing))\b"
)
_EDIT_ZH = re.compile(r"修改|修复|实现|写入|创建|更新")
_NEGATOR_EN = r"(?:do\s+not|don'?t|never|not|no\s+need\s+to|without)"
_NEGATOR_ZH = r"(?:不要|不用|无需|不需要|不必|别|请勿|勿|禁止|不准|不许|不得|不能|不)"
_CHANGE_EN = r"(?:re)?(?:fix|implement|writ|edit|modif|creat|patch|updat|chang|touch|alter)\w*"
_CHANGE_ZH = r"(?:修改|改动|更改|变更|改变|修复|更新|创建|写入|实现|改)"
# Mentions that are not requests: negated verbs, and "how was it implemented" questions.
_NOT_A_REQUEST = re.compile(
    rf"\b{_NEGATOR_EN}\s+(?:\w+\s+)?{_CHANGE_EN}"
    rf"|{_NEGATOR_ZH}\s*(?:再|去|做|作|进行)?\s*(?:任何)?{_CHANGE_ZH}"
    r"|(?:怎么|如何|怎样)[^，。,.!?？！；;\n]{0,12}?实现的"
)
_WORKSPACE_EN = r"(?:files?|code(?:base)?|source(?:\s+code)?|repo(?:sitory)?|workspace|project|directory|folder|anything)"
_WORKSPACE_ZH = r"(?:文件|代码|源码|源代码|仓库|工作区|项目|东西|内容)"
_CLAUSE_END_EN = r"(?=\s*(?:$|[.,;:!?)\]]|(?:and|but|just|only|please|at\s+all|in\s+any\s+way)\b))"
_CLAUSE_END_ZH = r"(?=$|[。．.!！?？,，;；、:：)）\s]|只|仅|请|就|即|直接|也)"
_EXCEPT = r"(?!\s+(?:to|in|outside|other|except|beyond|besides|apart))"
_READ_ONLY = re.compile(
    rf"\b{_NEGATOR_EN}\s+(?:\w+\s+)?(?:modif|chang|edit|touch|alter)\w*"
    rf"(?:\s+(?:any|the|these|this|my|our|existing))?(?:\s+{_WORKSPACE_EN})?{_CLAUSE_END_EN}"
    rf"|\b(?:make\s+no|(?:do\s+not|don'?t|never)\s+make\s+any|without\s+(?:making\s+)?any)"
    rf"\s+(?:code\s+|file\s+)?changes\b{_EXCEPT}"
    rf"|\bno\s+(?:code|file)\s+changes\b{_EXCEPT}"
    rf"|\bno\s+changes\s+to\s+(?:the\s+|any\s+)?{_WORKSPACE_EN}\b"
    rf"|\bread[- ]only\b"
    rf"|{_NEGATOR_ZH}\s*(?:再|去|做|作|进行)?\s*(?:任何)?(?:修改|改动|更改|变更|改变|改)\s*(?:任何)?{_WORKSPACE_ZH}?{_CLAUSE_END_ZH}"
    rf"|{_NEGATOR_ZH}\s*对\s*(?:任何)?{_WORKSPACE_ZH}\s*(?:做|作|进行)?\s*(?:任何)?(?:修改|改动|更改|变更|改变)"
    r"|只读"
)
_VERIFY = re.compile(r"(?<![a-z])(?:py|unit)?tests?(?:ing|ed|cases?)?(?![a-z])|\bverification\b|验证|测试")
_OBSERVE = re.compile(r"\b(?:inspect|review|check)|查看|检查|分析")


class Verifier:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def observe(self, state: RunState, outcome: Outcome) -> None:
        if outcome.status == "success" and outcome.data.get("path"):
            state.observed = True
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
            state.verification = Verification(
                state="missing", workspace_hash=self.workspace.fingerprint()
            )
        if outcome.data.get("is_verification"):
            anonymous = outcome.data.get("test_evidence_source") in {"exit_code", "inline_script"}
            for test, status in outcome.data.get("test_results", {}).items():
                if status == "failed" and anonymous:
                    # Evidence without a test identity (an exit code; an assertion script under a
                    # fixed id) cannot be rerun as "the same test". It fails verification only.
                    continue
                if status == "failed":
                    state.verification_failures[test] = outcome.data.get("command", "")
                    state.environment_failures.pop(test, None)
                elif status == "passed":
                    state.verification_failures.pop(test, None)
                    state.environment_failures.pop(test, None)
                elif status == "network_unavailable":
                    # Cannot pass without network; reported as unverified, never as fixed.
                    state.verification_failures.pop(test, None)
                    state.environment_failures[test] = outcome.data.get("command", "")
            state.baseline_signatures.update(outcome.data.get("baseline_signatures", {}))
            # Latest evidence wins: a test stays a pre-existing candidate only while it keeps
            # failing exactly as it does on the original files.
            identical = set(outcome.data.get("baseline_identical", []))
            for test, status in outcome.data.get("test_results", {}).items():
                if status == "failed" and test in identical:
                    # Pinned to the tested code: any later edit voids it until the test reruns.
                    state.preexisting_failures[test] = outcome.data.get("tested_workspace_hash", "")
                else:
                    state.preexisting_failures.pop(test, None)
        if (
            outcome.data.get("is_verification")
            and (outcome.status == "success" or outcome.data.get("network_only_failures"))
            and outcome.reason != "no_tests_collected"
        ):
            state.verification = Verification(
                state="failed" if state.verification_failures else "passed",
                workspace_hash=self.workspace.fingerprint(),
                evidence_id=outcome.evidence_id,
                command=outcome.data.get("command", ""),
            )
        elif outcome.data.get("is_verification"):
            state.verification = Verification(
                state="failed",
                workspace_hash=self.workspace.fingerprint(),
                evidence_id=outcome.evidence_id,
            )

    def accepted_preexisting(self, state: RunState) -> dict[str, str]:
        """Failures that stop blocking final delivery. All must hold: the agent changed code,
        was shown this evidence at a completion check at least once, and the test still
        fails exactly as on the original files when run against the current workspace."""
        if not state.changed_paths or state.final_revisions < 1:
            return {}
        current = self.workspace.fingerprint()
        return {test: state.verification_failures[test] for test, tested in state.preexisting_failures.items()
                if test in state.verification_failures and tested == current}

    def final(self, state: RunState) -> VerifierResult:
        task = state.task
        if (
            state.verification.state == "passed"
            and state.verification.workspace_hash != self.workspace.fingerprint()
        ):
            state.verification.state = "missing"
        if task.require_observation and not state.observed:
            return VerifierResult(passed=False, reason="observation_missing")
        if task.require_edit and not state.changed_paths:
            return VerifierResult(passed=False, reason="edit_missing")
        accepted = self.accepted_preexisting(state)
        blocking = {test: command for test, command in state.verification_failures.items()
                    if test not in accepted}
        if task.require_verification and blocking:
            return VerifierResult(passed=False, reason="verification_unresolved_failures",
                                  details={"failures": blocking})
        verified = state.verification.state == "passed" or (
            accepted and state.verification.state == "failed"
            and state.verification.workspace_hash == self.workspace.fingerprint()
        )
        if task.require_verification and not verified:
            return VerifierResult(passed=False, reason=f"verification_{state.verification.state}")
        if state.pending_requirements:
            return VerifierResult(passed=False, reason="requirements_unreviewed",
                                  details={"todo_ids": [item["id"] for item in state.pending_requirements]})
        for name in task.required_artifacts:
            try:
                if self.workspace.path(name).is_file():
                    continue
            except ValueError:
                pass
            return VerifierResult(passed=False, reason=f"artifact_missing:{name}")
        if state.cancelled_requirements:
            return VerifierResult(passed=False, reason="requirements_cancelled")
        return VerifierResult(
            passed=True,
            reason="requirements_satisfied",
            details={"changed_paths": state.changed_paths,
                     **({"environment_failures": state.environment_failures}
                        if state.environment_failures else {}),
                     **({"preexisting_failures": accepted} if accepted else {})},
        )

    @staticmethod
    def infer(prompt: str) -> dict[str, bool]:
        text = prompt.casefold()
        requested = _NOT_A_REQUEST.sub(" ", text)
        edit = bool(_EDIT_EN.search(requested) or _EDIT_ZH.search(requested)) and not _READ_ONLY.search(text)
        return {
            "require_edit": edit,
            "require_verification": edit and bool(_VERIFY.search(text)),
            "require_observation": bool(_OBSERVE.search(text)) or edit,
        }
