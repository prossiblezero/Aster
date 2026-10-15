"""Task and workspace verification; task success never comes from loop termination."""

from __future__ import annotations

import json

from ..schema import Outcome, RunState, Verification, VerifierResult
from ..storage.workspace import Workspace


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
            state.baseline_results.update(outcome.data.get("baseline_results", {}))
            # The tool preview may have clipped the appended baseline explanation.
            note = outcome.data.get("baseline_feedback", "")
            if note and note not in outcome.content:
                outcome.content += "\n" + note
            accepted, rejected = [], []
            for test in dict.fromkeys(outcome.data.get("preexisting", [])):
                if state.baseline_results.get(test) == "failed":
                    command = state.verification_failures.pop(test, outcome.data.get("command", ""))
                    state.preexisting_failures[test] = command
                    accepted.append(test)
                else:
                    rejected.append(test)
            if accepted or rejected:
                outcome.data.update(accepted_preexisting=accepted, rejected_preexisting=rejected)
                outcome.content += (
                    f"\nAccepted as pre-existing: {json.dumps(accepted)}. "
                    f"Not accepted (no baseline failure on record): {json.dumps(rejected)}"
                )
            for test, status in outcome.data.get("test_results", {}).items():
                if status == "failed" and test not in state.preexisting_failures:
                    state.verification_failures[test] = outcome.data.get("command", "")
                elif status == "passed":
                    state.verification_failures.pop(test, None)
                    state.preexisting_failures.pop(test, None)
        declared_only = (
            outcome.status == "error" and outcome.reason == "tests_failed"
            and bool(outcome.data.get("test_results"))
            and all(status == "passed" or test in state.preexisting_failures
                    for test, status in outcome.data["test_results"].items())
        )
        if (
            outcome.data.get("is_verification")
            and (outcome.status == "success" or declared_only)
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
        if task.require_verification and state.verification_failures:
            return VerifierResult(passed=False, reason="verification_unresolved_failures",
                                  details={"failures": state.verification_failures})
        if task.require_verification and state.verification.state != "passed":
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
                     "preexisting_failures": dict(state.preexisting_failures)},
        )

    @staticmethod
    def infer(prompt: str) -> dict[str, bool]:
        text = prompt.casefold()
        edit_requested = any(
            x in text
            for x in (
                "fix",
                "implement",
                "write",
                "edit",
                "modify",
                "create",
                "patch",
                "update",
                "修改",
                "修复",
                "实现",
                "写入",
                "创建",
                "更新",
            )
        )
        edit_negated = any(
            x in text
            for x in (
                "do not modify",
                "don't modify",
                "without modifying",
                "without changing",
                "read-only",
                "read only",
                "不要修改",
                "无需修改",
                "只读",
            )
        )
        edit = edit_requested and not edit_negated
        verify = any(x in text for x in ("test", "tests", "pytest", "verification", "验证", "测试"))
        observe = any(x in text for x in ("inspect", "review", "check", "查看", "检查", "分析"))
        return {
            "require_edit": edit,
            "require_verification": edit and verify,
            "require_observation": observe or edit,
        }
