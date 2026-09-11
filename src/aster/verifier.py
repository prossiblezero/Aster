"""Task and workspace verification; task success never comes from loop termination."""

from __future__ import annotations

from .schema import Outcome, RunState, Verification, VerifierResult
from .workspace import Workspace


class Verifier:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def observe(self, state: RunState, outcome: Outcome) -> None:
        if outcome.status == "success" and outcome.data.get("path"):
            state.observed = True
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
            state.verification = Verification(state="missing", workspace_hash=self.workspace.fingerprint())
        if outcome.status == "success" and outcome.data.get("is_verification"):
            state.verification = Verification(state="passed", workspace_hash=self.workspace.fingerprint(),
                                              evidence_id=outcome.evidence_id, command=outcome.data.get("command", ""))
        elif outcome.status in {"error", "timeout"} and outcome.data.get("is_verification"):
            state.verification = Verification(state="failed", workspace_hash=self.workspace.fingerprint(),
                                              evidence_id=outcome.evidence_id)

    def final(self, state: RunState) -> VerifierResult:
        task = state.task
        if task.require_observation and not state.observed:
            return VerifierResult(passed=False, reason="observation_missing")
        if task.require_edit and not state.changed_paths:
            return VerifierResult(passed=False, reason="edit_missing")
        if task.require_verification and state.verification.state != "passed":
            return VerifierResult(passed=False, reason=f"verification_{state.verification.state}")
        return VerifierResult(passed=True, reason="requirements_satisfied",
                              details={"changed_paths": state.changed_paths})

    @staticmethod
    def infer(prompt: str) -> dict[str, bool]:
        text = prompt.casefold()
        edit = any(x in text for x in ("fix", "implement", "write", "edit", "修改", "修复", "实现", "写入"))
        verify = any(x in text for x in ("test", "pytest", "验证", "测试"))
        observe = any(x in text for x in ("inspect", "review", "check", "查看", "检查", "分析"))
        return {"require_edit": edit, "require_verification": edit and verify,
                "require_observation": observe or edit}
