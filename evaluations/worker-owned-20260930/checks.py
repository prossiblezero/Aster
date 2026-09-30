"""One offline contract check; temporary synthetic accounting, no model calls."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path

import evaluate as driver

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize as report


def rejected(action, message):
    try:
        action()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("accepted invalid input: " + message)


def main():
    tasks = driver.bank()
    assert len(driver.schedule(tasks)) == 60 and len(driver.gates()) == 10
    with tempfile.TemporaryDirectory(prefix="aster-owned-offline-check-") as directory:
        root = Path(directory)
        report.OUT, report.HERE = root / "private", root / "report"
        task = tasks[0]
        report.bank = lambda: [task]
        source = {"root": "/fake/new", "commit": "new"}
        sources = {"solo": source, "worker": source,
                   "current": {"root": "/fake/old", "commit": driver.selection()["current_commit"]}}
        binding_path = root / "bindings.json"
        driver.save(binding_path, sources)
        assert driver.binding(binding_path) == sources
        invalid = copy.deepcopy(sources)
        invalid["worker"] = source | {"commit": "different"}
        driver.save(binding_path, invalid)
        rejected(lambda: driver.binding(binding_path), "require_old_current_and_same_new_solo_worker")
        frozen = {"sources": sources, "driver_hashes": {"driver": "frozen"},
            "source_hashes": {arm: {"schema.py": sources[arm]["commit"]} for arm in driver.ARMS},
            "settings": {arm: {"model": "offline-fixture", "max_workers": 0 if arm == "solo" else 2}
                         for arm in driver.ARMS},
            "endpoint_sha256": dict.fromkeys(driver.ARMS, "endpoint"),
            "gates": {task["id"]: {"sha256": "gate"}}, "environments": {"fixture-python": {}}}
        driver.save(report.OUT / "run-lock.json", frozen)

        def summarize():
            with contextlib.redirect_stdout(io.StringIO()):
                report.main()
            return json.loads((report.HERE / "summary.json").read_text())

        empty = summarize()
        assert empty["finished_episodes"] == 0 and len(empty["missing_episodes"]) == 6
        paths = []
        for repeat in driver.REPEATS:
            for arm in driver.ARMS:
                passed = (repeat, arm) not in {(1, "worker"), (2, "solo")}
                normal = passed and (repeat, arm) != (1, "current")
                child = arm == "worker" and repeat == 2
                usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
                shared = {"task": task["id"], "arm": arm, "repeat": repeat,
                    "source_commit": sources[arm]["commit"], "source_root": sources[arm]["root"],
                    "internal_team_token_budget": driver.legacy.LIMITS[task["difficulty"]]["reported_tokens"],
                    "preflight_sha256": "gate"}
                launch = shared | {"driver_hashes": frozen["driver_hashes"],
                    "source_hashes": frozen["source_hashes"][arm], "settings": frozen["settings"][arm],
                    "endpoint_sha256": "endpoint", "limits": driver.legacy.LIMITS[task["difficulty"]],
                    "python": "fixture-python", "aster_import": sources[arm]["root"] + "/src/aster/__init__.py",
                    "prompt": task["prompt"] + driver.legacy.COMMON + "\n\n", "preparation": {}}
                runs = [{"run_id": "parent", "parent_run_id": None, "phase": "completed", "usage": usage}]
                tools = [{"name": "worker_spawn"}] if child else []
                if child:
                    runs[0]["usage"] = {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}
                    runs.append({"run_id": "child", "parent_run_id": "parent", "phase": "completed",
                                 "usage": dict(runs[0]["usage"])})
                attempts = [{"classification": "success", "usage": usage}]
                if (repeat, arm) == (1, "worker"):
                    attempts.append({"classification": "transport_error", "usage": {}})
                row = shared | {"source": task["source"], "difficulty": task["difficulty"],
                    "passed": passed, "grade": {"passed": passed}, "phase": "completed" if normal else "stopped",
                    "stop_reason": "verified" if normal else "budget", "error": None, "seconds": 3,
                    "usage": usage, "ledger_usage": usage, "usage_matches": True, "source_unchanged": True,
                    "runs": runs, "tools": tools, "tool_calls": len(tools), "coordination_calls": len(tools),
                    "model_calls": 1, "http_attempts": len(attempts), "http_with_total_usage": 1,
                    "workers_started": int(child), "workers_completed": int(child), "product_before": {"code.py": "starter"}}
                path = report.OUT / f"repeat-{repeat:02d}/runs/{task['id']}--{arm}"
                for name, data in (("launch", launch), ("result", row),
                                   ("request-ledger", [{"usage": usage, "attempts": attempts}])):
                    driver.save(path / (name + ".json"), data)
                paths.append(path)
                if len(paths) == 1:
                    partial = summarize()
                    assert partial["finished_episodes"] == 1 and len(partial["missing_episodes"]) == 5
        summary = summarize()
        assert summary["finished_episodes"] == 6 and summary["complete_triplets"] == 2
        assert summary["all_passed"]["triplets"] == 0
        assert summary["comparisons"]["solo_vs_worker"]["both_passed"]["pairs"] == 0
        assert summary["comparisons"]["solo_vs_current"]["both_passed"]["pairs"] == 1
        assert summary["comparisons"]["current_vs_worker"]["both_correct_normal_delivery"]["pairs"] == 1
        assert summary["all"]["current"]["passed"] == 2 and summary["all"]["current"]["correct_normal_delivery"] == 1
        assert summary["all"]["worker"]["unknown_http_classifications"] == {"transport_error": 1}
        assert summary["all"]["worker"]["usage_by_role"]["worker"]["total_tokens"] == 6
        for filename, field, value, message in (
            ("launch", "source_hashes", {}, "launch_differs_from_frozen_run"),
            ("launch", "internal_team_token_budget", 1, "episode_task_or_budget_mismatch"),
            ("launch", "prompt", "contaminated", "episode_task_or_budget_mismatch"),
            ("result", "usage", {"total_tokens": 1}, "source_or_usage_integrity_failure"),
            ("result", "workers_started", 99, "parent_child_or_tool_integrity_failure"),
        ):
            path = paths[0] / (filename + ".json")
            original = json.loads(path.read_text())
            driver.save(path, original | {field: value})
            rejected(summarize, message)
            driver.save(path, original)
        path = paths[-1] / "result.json"
        failed = json.loads(path.read_text())
        failed.update(passed=False, grade={"passed": False, "status": "grader_error"}, error="RuntimeError",
            phase=None, stop_reason=None, runs=[], tools=[], usage={}, ledger_usage={}, model_calls=0,
            http_attempts=0, http_with_total_usage=0, tool_calls=0, coordination_calls=0,
            workers_started=0, workers_completed=0)
        driver.save(path, failed)
        (path.parent / "request-ledger.json").unlink()
        (report.OUT / "repeat-02/runs/interrupted").mkdir()
        failed_summary = summarize()
        assert failed_summary["finished_episodes"] == 6
        assert failed_summary["all"]["worker"]["episode_errors"] == {"RuntimeError": 1}
        assert failed_summary["all"]["worker"]["grade_errors"] == 1
        assert len(failed_summary["incomplete_directories"]) == 1
    print("offline check passed: selection, binding, empty/partial/full results, unknown usage, corruption rejection")


if __name__ == "__main__":
    main()
