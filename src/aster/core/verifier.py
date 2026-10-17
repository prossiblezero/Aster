"""Task and workspace verification; task success never comes from loop termination."""

from __future__ import annotations

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
