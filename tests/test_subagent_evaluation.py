"""Evaluation failures must remain failures with unknown cost, never cheap wins."""
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def evaluation():
    path = REPO / "evaluations/subagent-ten-20261003/evaluate.py"
    spec = importlib.util.spec_from_file_location("ten_evaluation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("partial_usage", [{}, {"total_tokens": 100}])
def test_unknown_team_usage_is_not_zero_cost(evaluation, tmp_path, monkeypatch, partial_usage):
    monkeypatch.setattr(evaluation, "OUT", tmp_path / "private")
    monkeypatch.setattr(evaluation, "HERE", tmp_path / "public")
    order = [dict(task="task", arm=arm, repeat=1) for arm in ("solo", "team")]
    frozen = dict(order=order, source={}, settings={})
    evaluation.save(evaluation.OUT / "test/freeze.json", frozen)
    for plan in order:
        known = plan["arm"] == "solo"
        row = plan | dict(passed=False, grade={"correct": 0, "parse_error": True},
                          behavior_correct=0,
                          usage={"total_tokens": 100, "input_tokens_requests": 1} if known else partial_usage,
                          seconds=1, parent_input_tokens=100 if known else 0,
                          parent_model_calls=1, model_calls=1,
                          requests_without_usage=0 if known or partial_usage else 1, http_without_usage=0,
                          finish_rejected=0)
        evaluation.save(evaluation.OUT / f"test/runs/task--{plan['arm']}--1/result.json", row)
        evaluation.save(evaluation.OUT / f"test/runs/task--{plan['arm']}--1/launch.json",
                        plan | dict(source={}, settings={}))
    evaluation.summarize("test")
    pairs = json.loads((evaluation.HERE / "test/pairs.json").read_text())
    assert pairs[0]["tokens"] == (1.0 if partial_usage else None)
    assert pairs[0]["parent_input_tokens"] is None


@pytest.mark.parametrize("malformed", [{"symbol": []}, {"symbol": {}}, None])
def test_malformed_citation_is_a_scored_failure(evaluation, malformed):
    base = evaluation.shared.base(REPO / "src")
    gold = [dict(id="q", observed=1, evidence_path="m.py", evidence_ranges={"f": [1, 2]})]
    answer = json.dumps({"answers": [dict(id="q", observed=1, evidence=malformed)]})
    result = evaluation.grade(base, answer, gold, False)
    assert not result["passed"] and result["correct"] == 0 and result["total"] == 1


def test_legacy_input_completeness_counts_http_attempts(evaluation):
    row = dict(parent_model_calls=2, model_calls=2, requests_without_usage=0, http_without_usage=0,
               usage={"total_tokens": 100, "usage_requests": 3, "input_tokens_requests": 2})
    assert not evaluation.parent_input_complete(row)
    row["usage"]["input_tokens_requests"] = 3
    assert evaluation.parent_input_complete(row)
    row["http_without_usage"] = 1
    assert not evaluation.parent_input_complete(row)
    row["usage"] = {}
    assert not evaluation.parent_input_complete(row)


@pytest.mark.parametrize("saved", ["valid", "truncated", "wrong_identity", "wrong_source", "foreign_freeze", "legacy_result"])
def test_resume_audits_completed_results_and_refreshes_summary(evaluation, tmp_path, monkeypatch, saved):
    monkeypatch.setattr(evaluation, "OUT", tmp_path)
    planned = dict(task="task", arm="solo", repeat=1)
    frozen = dict(order=[planned], source={"module.py": "expected-source"}, settings={},
                  result_identity="freeze_sha256")
    freeze_path = tmp_path / "r/freeze.json"
    evaluation.save(freeze_path, frozen)
    fingerprint = evaluation.digest(freeze_path)
    target = tmp_path / "r/runs/task--solo--1/result.json"
    result = planned | {"freeze_sha256": fingerprint}
    if saved == "wrong_identity":
        result["repeat"] = 2
    elif saved == "foreign_freeze":
        result["freeze_sha256"] = "another-round"
    elif saved == "legacy_result":
        result.pop("freeze_sha256")
    evaluation.save(target, result)
    launch = planned | dict(source=frozen["source"], settings={}, freeze_sha256=fingerprint)
    if saved == "wrong_source":
        launch["source"] = {"module.py": "another-source"}
    evaluation.save(target.with_name("launch.json"), launch)
    if saved == "truncated":
        target.write_text('{"task":')
    original = target.read_bytes()
    summaries = []
    monkeypatch.setattr(evaluation, "summarize", summaries.append)
    if saved == "valid":
        evaluation.run("r")
        assert summaries == ["r"]
    else:
        with pytest.raises(RuntimeError, match="requires_audit"):
            evaluation.run("r")
        assert not summaries
    assert target.read_bytes() == original


def test_interrupted_freeze_resumes_without_publishing_incomplete_archive(evaluation, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "OUT", tmp_path / "private")
    monkeypatch.setattr(evaluation, "HERE", tmp_path / "public")
    monkeypatch.setattr(evaluation, "DATA", tmp_path / "data")
    monkeypatch.setattr(evaluation, "REPO", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("value = 1\n")
    driver = tmp_path / "driver.py"
    driver.write_text("pass\n")
    config = SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: ""), auth="none",
                             identity=lambda: {}, base_url="https://model.invalid")
    base = SimpleNamespace(settings=lambda _: config)
    monkeypatch.setattr(evaluation.shared, "base", lambda _: base)
    monkeypatch.setattr(evaluation, "preflight", lambda *args: [])
    monkeypatch.setattr(evaluation.cases, "bank", lambda _: [{"id": "task"}])
    monkeypatch.setattr(evaluation, "environment", lambda: {})
    monkeypatch.setattr(evaluation, "harness_hashes", lambda: {"driver.py": evaluation.digest(driver)})
    copy = evaluation.shutil.copy2

    def interrupted(*args, **kwargs):
        raise OSError("archive interrupted")

    monkeypatch.setattr(evaluation.shutil, "copy2", interrupted)
    with pytest.raises(OSError, match="archive interrupted"):
        evaluation.freeze("r", source, 1)
    assert not (evaluation.OUT / "r/freeze.json").exists()
    monkeypatch.setattr(evaluation.shutil, "copy2", copy)
    evaluation.freeze("r", source, 1)
    evaluation.inputs("r")
    archive = evaluation.OUT / "r/original-harness/driver.py"
    archive.write_text("changed\n")
    with pytest.raises(AssertionError):
        evaluation.inputs("r")


def test_simultaneous_summaries_use_distinct_staging_files(evaluation, tmp_path, monkeypatch):
    target = tmp_path / "summary.json"
    barrier = threading.Barrier(2)
    replace = Path.replace

    def simultaneous_replace(path, destination):
        barrier.wait(timeout=5)
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", simultaneous_replace)
    values = [{"writer": 1, "rows": [1] * 100}, {"writer": 2, "rows": [2] * 200}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda value: evaluation.save(target, value), values))
    assert json.loads(target.read_text()) in values
    assert list(tmp_path.iterdir()) == [target]


def test_adaptive_settings_are_explicit_and_do_not_mutate_solo(evaluation, tmp_path):
    from aster.config import Settings

    config = Settings(max_workers=0, read_only=True, network=False, approval="auto")
    solo = evaluation.arm_settings(config, "adaptive", "solo")
    team = evaluation.arm_settings(config, "adaptive", "team")
    assert solo is config and config.max_workers == 0
    assert team.max_workers == 1
    assert team.identity() | {"max_workers": 0} == config.identity()
    assert evaluation.arm_settings(config, "parent_child", "team") is config
    planned = dict(task="task", arm="team", repeat=1)
    frozen = dict(strategy="adaptive", settings=config.identity(), source={},
                  arm_settings={"solo": solo.identity(), "team": team.identity()})
    path = tmp_path / "result.json"
    evaluation.save(path, planned)
    launch = planned | dict(source={}, settings=team.identity())
    evaluation.save(path.with_name("launch.json"), launch)
    assert evaluation.completed_result(path, planned, frozen) == planned
    launch["settings"] = solo.identity()
    evaluation.save(path.with_name("launch.json"), launch)
    with pytest.raises(RuntimeError, match="requires_audit"):
        evaluation.completed_result(path, planned, frozen)
    del frozen["arm_settings"]
    with pytest.raises(RuntimeError, match="requires_audit"):
        evaluation.completed_result(path, planned, frozen)


def test_adaptive_request_removes_external_assignment_for_both_arms(evaluation, tmp_path, monkeypatch):
    base = evaluation.shared.base(REPO / "src")
    task = dict(kind="investigate", aliases={"m": "module.py"}, groups=[])
    original = base.prompt(task)

    def prepare(_base, _task, root):
        root.mkdir()
        (root / "module.py").write_text("value = 1\n")
        (root / "REQUEST.md").write_text(original)

    monkeypatch.setattr(evaluation.cases, "prepare", prepare)
    requests = []
    for arm in ("solo", "team"):
        root = tmp_path / arm
        request = evaluation.prepare(base, task, root, "adaptive")
        assert request == (root / "REQUEST.md").read_text()
        assert "Do not start additional subagents" not in request
        assert "experiment controls assignment" not in request
        assert "both must be covered" in request
        assert (root / "module.py").read_text() == "value = 1\n"
        requests.append(request)
    assert requests[0] == requests[1]
    assert evaluation.prepare(base, task, tmp_path / "legacy", "parent_child") == original


@pytest.fixture
def comparison(evaluation, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "evaluations/subagent-ten-20261003"))
    monkeypatch.setitem(sys.modules, "evaluate", evaluation)
    path = REPO / "evaluations/subagent-ten-20261003/compare.py"
    return evaluation.load("ten_comparison_test", path)


def comparison_rows():
    return [dict(task="task", arm=arm, repeat=1, passed=True, behavior_correct=1,
                 grade=dict(correct=1, total=1, parse_error=False),
                 usage=dict(total_tokens=100), requests_without_usage=0, http_without_usage=0,
                 model_calls=1, parent_model_calls=1, parent_input_tokens=90, seconds=1,
                 runs=[dict(run_id=arm, parent_run_id=None)]) for arm in ("solo", "team")]


def test_zero_reported_tokens_keep_pair_but_have_no_token_ratio(comparison):
    rows = comparison_rows()
    rows[0]["usage"]["total_tokens"] = 0
    result = comparison.metrics(rows)
    assert result["complete_usage"] == dict(pairs=1, tokens=dict(solo=0, team=100),
                                           ratio_of_totals=None, median_token_ratio=None)
    rows[1]["requests_without_usage"] = 1
    result = comparison.metrics(rows)
    assert result["arms"]["team"]["strict_pass"] == 1
    assert result["complete_usage"]["pairs"] == 0


@pytest.mark.parametrize("missing", ["all", "trace.jsonl", "request-ledger.json"])
def test_incomplete_trace_manifest_cannot_publish_verified_comparison(comparison, tmp_path, monkeypatch, missing):
    c = comparison
    monkeypatch.setattr(c, "OUT", tmp_path / "private")
    monkeypatch.setattr(c, "HERE", tmp_path / "public")
    dest = c.OUT / "r"
    rows = comparison_rows()
    order = [{k: row[k] for k in ("task", "arm", "repeat")} for row in rows]
    c.save(dest / "preflight.json", [dict(task="task", gold=[])])
    frozen = dict(order=order, source={}, settings={}, harness={},
                  preflight_sha256=c.digest(dest / "preflight.json"))
    c.save(dest / "freeze.json", frozen)
    artifacts, diagnostics = {}, []
    for row, planned in zip(rows, order, strict=True):
        name = f"task--{row['arm']}--1"
        run = dest / "runs" / name
        for filename, payload in [("result.json", row), ("launch.json", planned | dict(source={}, settings={})),
                                  ("request-ledger.json", [])]:
            c.save(run / filename, payload)
            artifacts[str((run / filename).relative_to(c.OUT))] = c.digest(run / filename)
        trace = dest / "workspaces" / name / ".aster/runs" / row["arm"] / "trace.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text("")
        artifacts[str(trace.relative_to(c.OUT))] = c.digest(trace)
        diagnostics.append(planned | dict(parent_lines_before_first_handoff=0,
                                          parent_child_overlap_before_first_handoff=0,
                                          parent_status_waits=[], parent_tool_load_requests=[]))
    c.save(c.HERE / "r/diagnostics.json", dict(episodes=2, planned=2, diagnostics=diagnostics, propagation={}))
    manifest = c.HERE / "r/TRACE-HASHES.json"
    c.save(manifest, artifacts)
    assert c.compare("r")["archive_inputs_and_result_identities_verified"]
    output = c.HERE / "r/comparisons.json"
    previous = output.read_bytes()
    if missing == "all":
        artifacts = {}
    else:
        artifacts.pop(next(path for path in artifacts if path.endswith(missing)))
    c.save(manifest, artifacts)
    with pytest.raises(AssertionError, match="incomplete_trace_manifest"):
        c.compare("r")
    assert output.read_bytes() == previous
