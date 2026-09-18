"""Synthetic offline contracts only; no evaluation sample is admitted to training."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec
from aster.training import (
    GRPORollout,
    PolicyDecision,
    TaskRecord,
    TokenizedDecision,
    prepare_sft,
    validate_group,
    validate_splits,
)
from aster.workspace import digest


def task_record(**updates):
    return TaskRecord.model_validate(
        {
            "task_id": "new-example-1",
            "repository": "new-repo-1",
            "repository_revision": "a" * 40,
            "issue_family": "new-family-1",
            "source": "synthetic-contract-fixture",
            "license": "MIT",
            "training_allowed": True,
            "origin": "new",
            "split": "train",
            "fixture_sha256": "b" * 64,
            "environment_sha256": "c" * 64,
            "verifier_sha256": "d" * 64,
            **updates,
        }
    )


def decision(**updates):
    return {
        "request_sha256": "a" * 64,
        "rendered_sample_sha256": "b" * 64,
        "input_ids": [10, 11, 12, 13],
        "prompt_length": 2,
        "assistant_mask": [False, False, True, True],
        "behavior_logprobs": [-0.5, -0.7],
        "reference_logprobs": [-0.8, -0.9],
        **updates,
    }


def rollout(**updates):
    return GRPORollout.model_validate(
        {
            "task": task_record(),
            "run_id": "run-1",
            "group_id": "group-1",
            "sample_index": 0,
            "seed": 17,
            "decisions": [decision()],
            "termination": "success",
            "reward": 1,
            "independent_verifier": True,
            "max_steps": 3,
            "max_turns": 2,
            "max_generated_tokens": 8,
            "max_seconds": 60,
            "tool_steps": 1,
            "elapsed_seconds": 2,
            "versions": {
                "actor_sha256": "1" * 64,
                "reference_sha256": "2" * 64,
                "tokenizer_sha256": "3" * 64,
                "chat_template_sha256": "4" * 64,
                "harness_revision": "5" * 40,
                "context_policy_version": "deterministic-test",
                "tool_schema_sha256": "6" * 64,
                "reward_version": "7" * 64,
                "reasoning_mode": "disabled",
                "sampling_parameters": {"temperature": 0.8, "top_p": 1.0},
            },
            **updates,
        }
    )


@pytest.mark.parametrize("origin", ["existing_evaluation", "existing_practice"])
def test_existing_evaluation_and_practice_stay_audit_only(origin):
    for split in ("train", "dev", "test"):
        with pytest.raises(ValidationError, match="audit-only"):
            task_record(origin=origin, split=split)
    assert task_record(origin=origin, split="audit").split == "audit"


def test_training_license_and_repository_family_splits_are_required():
    with pytest.raises(ValidationError, match="license admission"):
        task_record(training_allowed=False)
    first = task_record()
    for shared in ({"repository": first.repository}, {"issue_family": first.issue_family}):
        second = task_record(
            task_id="new-example-2",
            repository="new-repo-2",
            issue_family="new-family-2",
            split="test",
        )
        second = TaskRecord.model_validate({**second.model_dump(), **shared})
        with pytest.raises(ValueError, match="split leakage"):
            validate_splits([first, second])
    validate_splits(
        [
            first,
            task_record(
                task_id="new-example-2",
                repository="new-repo-2",
                issue_family="new-family-2",
                split="test",
            ),
        ]
    )


async def test_sft_uses_real_request_artifact_and_only_visible_assistant_output(tmp_path):
    class Teacher:
        async def complete(self, *_):
            return ModelReply(
                text="The next step is clear.",
                raw=[
                    {"type": "reasoning", "encrypted_content": "provider-opaque-target"},
                    {"role": "assistant", "content": "The next step is clear."},
                ],
            )

    runtime = Runtime(tmp_path, Settings(memory_enabled=False, auto_dream=False), model=Teacher())
    runtime.start(TaskSpec(prompt="Explain the next step"))
    runtime._structured_history = [
        {"type": "reasoning", "encrypted_content": "provider-opaque-input"},
        {"role": "assistant", "content": "Previous visible answer."},
        {"role": "user", "content": "Continue the explanation."},
    ]
    await runtime.run()
    events = runtime.evidence.events(runtime.state.run_id)
    context = next(event for event in events if event["event"] == "context_built")
    reply = next(event for event in events if event["event"] == "model_reply")
    artifact = json.loads((runtime.evidence.root / context["request_artifact"]).read_text())
    original = deepcopy(artifact)
    sample = prepare_sft(
        artifact, context, reply, task=task_record(), provider="teacher", protocol="responses"
    )
    assert sample.context_policy_version == artifact["policy_version"]
    assert sample.assistant_target == [{"role": "assistant", "content": "The next step is clear."}]
    assert sample.omitted_prompt_reasoning == 1
    assert "provider-opaque" not in sample.model_dump_json()
    assert "Previous visible answer." in sample.model_dump_json()
    assert artifact == original and "provider-opaque-input" in json.dumps(artifact)
    damaged = deepcopy(artifact)
    damaged["payload"]["model"] = "different"
    with pytest.raises(ValueError, match="provenance mismatch"):
        prepare_sft(
            damaged, context, reply, task=task_record(), provider="teacher", protocol="responses"
        )
    wrong_turn = {**reply, "turn": reply["turn"] + 1}
    wrong_turn["hash"] = digest(
        json.dumps(
            {k: v for k, v in wrong_turn.items() if k != "hash"}, sort_keys=True, ensure_ascii=False
        )
    )
    with pytest.raises(ValueError, match="provenance mismatch"):
        prepare_sft(
            artifact,
            context,
            wrong_turn,
            task=task_record(),
            provider="teacher",
            protocol="responses",
        )
    with pytest.raises(ValueError, match="only admitted train"):
        prepare_sft(
            artifact,
            context,
            reply,
            task=task_record(origin="existing_evaluation", split="audit"),
            provider="teacher",
            protocol="responses",
        )


async def test_sft_rejects_same_turn_preview_and_legacy_unbound_reply(tmp_path):
    class Teacher:
        async def complete(self, messages, _tools):
            assert "Actual request" in json.dumps(messages)
            return ModelReply(text="Answer based on the actual request")

    runtime = Runtime(tmp_path, Settings(memory_enabled=False, auto_dream=False), model=Teacher())
    runtime.start(TaskSpec(prompt="Explain the next step"))
    runtime.state.plan = "Preview only"
    runtime.prepare_request()
    runtime.state.plan = "Actual request"
    await runtime.run()
    events = runtime.evidence.events(runtime.state.run_id)
    preview, actual = [event for event in events if event["event"] == "context_built"]
    reply = next(event for event in events if event["event"] == "model_reply")
    assert preview["request_sha256"] != actual["request_sha256"]
    preview_artifact = json.loads((runtime.evidence.root / preview["request_artifact"]).read_text())
    actual_artifact = json.loads((runtime.evidence.root / actual["request_artifact"]).read_text())
    with pytest.raises(ValueError, match="provenance mismatch"):
        prepare_sft(
            preview_artifact,
            preview,
            reply,
            task=task_record(),
            provider="teacher",
            protocol="responses",
        )
    legacy = {key: value for key, value in reply.items() if key not in {"hash", "request_sha256"}}
    legacy["hash"] = digest(json.dumps(legacy, sort_keys=True, ensure_ascii=False))
    with pytest.raises(ValueError, match="provenance mismatch"):
        prepare_sft(
            actual_artifact,
            actual,
            legacy,
            task=task_record(),
            provider="teacher",
            protocol="responses",
        )


def test_assistant_mask_never_supervises_prompt_or_tool_observations():
    fields = decision()
    fields.pop("behavior_logprobs")
    fields.pop("reference_logprobs")
    sample = TokenizedDecision.model_validate(fields)
    assert sample.labels == [-100, -100, 12, 13]
    for updates in (
        {"assistant_mask": [True, False, True, True]},
        {"assistant_mask": [False] * 4},
        {"assistant_mask": [False, True]},
        {"prompt_length": 4},
        {"input_ids": [10, 11, -1, 13]},
        {"input_ids": [10, 11, True, 13]},
    ):
        with pytest.raises(ValidationError):
            TokenizedDecision.model_validate({**fields, **updates})


@pytest.mark.parametrize(
    "updates",
    [
        {"behavior_logprobs": [-0.1]},
        {"reference_logprobs": [-0.2, float("nan")]},
        {"behavior_logprobs": [-0.2, 0.1]},
    ],
)
def test_probabilities_are_complete_finite_and_aligned(updates):
    with pytest.raises(ValidationError):
        PolicyDecision.model_validate(decision(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"independent_verifier": False},
        {"termination": "infrastructure_error"},
        {"termination": "verifier_error"},
        {"termination": "cancelled"},
        {"tool_steps": 4},
        {"tool_steps": 2},
        {"max_generated_tokens": 1},
        {"elapsed_seconds": 61},
        {"reward": float("inf")},
    ],
)
def test_rollouts_reject_untrusted_reward_and_silent_budget_overrun(updates):
    with pytest.raises(ValidationError):
        rollout(**updates)


def test_group_freezes_versions_task_and_budget_but_allows_different_sampling_seeds():
    first = rollout()
    second = rollout(
        run_id="run-2", sample_index=1, seed=18, reward=-1, termination="candidate_failure"
    )
    validate_group([first, second])
    for field, value in (("actor_sha256", "8" * 64), ("context_policy_version", "next-context")):
        changed = {**second.versions.model_dump(), field: value}
        with pytest.raises(ValueError, match="freeze"):
            validate_group([first, rollout(run_id="run-2", sample_index=1, versions=changed)])
    with pytest.raises(ValidationError):
        rollout(versions={**first.versions.model_dump(), "actor_sha256": "latest"})
    ungraded = rollout(run_id="run-2", sample_index=1, termination="verifier_error", reward=None)
    with pytest.raises(ValueError, match="graded"):
        validate_group([first, ungraded])
    with pytest.raises(ValueError, match="duplicate"):
        validate_group([first, first])
