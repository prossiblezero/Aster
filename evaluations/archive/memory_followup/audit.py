"""Read saved evidence to verify independent grades, candidate ownership and Memory provenance."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMIT = "d20c38fface3751fd366e303e77c81d6ce66c6f8"


def load(path):
    return json.loads(path.read_text())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    return " ".join(text.split())


def audit():
    conditions, identities = {}, []
    for condition in ("off", "on"):
        phases, all_events = [], {}
        for phase in (1, 2):
            evidence = HERE / condition / "evidence" / f"phase{phase}"
            events = [
                json.loads(line) for line in (evidence / "run/trace.jsonl").read_text().splitlines()
            ]
            result = load(HERE / condition / f"phase{phase}.json")
            launch = load(evidence / "launch.json")
            all_events[result["run_id"]] = events
            assert result["source_commit"] == launch["source_commit"] == COMMIT
            assert (
                result["source_unchanged"]
                and result["experiment_unchanged"]
                and result["seed_unchanged"]
            )
            assert result["operator_feedback"] == result["operator_candidate_repairs"] == 0
            assert result["turns"] <= 16 and result["steps"] <= 30 and result["seconds"] < 1800
            grade = result["independent_grade"]
            assert grade["tests"]["expected"] == grade["tests"]["executed"] == 7 + (phase == 2)
            assert grade["isolation"] == "landlock+seccomp"
            assert result["before_grade"]["tests"]["executed"] == 7 + (phase == 2)
            if phase == 1:
                assert not result["before_grade"]["passed"]
                assert result["memory"]["before"]["lessons"] == []
            for name, digest in launch["experiment_files"].items():
                assert sha256((HERE / name).read_bytes()) == digest
            for name, digest in launch["seed_files"].items():
                assert sha256((HERE / condition / "candidate" / name).read_bytes()) == digest
            identities.append(
                {
                    "source": launch["source"],
                    "connection_sha256": launch["connection_sha256"],
                    "experiment_files": launch["experiment_files"],
                    "configuration": {
                        key: value
                        for key, value in launch["configuration"].items()
                        if key != "memory_enabled"
                    },
                }
            )
            assert launch["configuration"]["memory_enabled"] is (condition == "on")
            first_context = next(event for event in events if event["event"] == "context_built")
            request_name = Path(first_context["request_artifact"]).name
            first = load(evidence / "run/requests" / request_name)["payload"]
            assert len(first["input"]) == 3  # A fresh task has no prior conversation.
            before = result["memory"]["before"]["lessons"]
            text = "\n".join(item["content"] for item in first["input"])
            capability_visible = "Memory capability:" in text
            load_tool = next(tool for tool in first["tools"] if tool["name"] == "tool_load")
            discoverable = (
                "memory_store" in load_tool["parameters"]["properties"]["names"]["items"]["enum"]
            )
            assert capability_visible is (condition == "on")
            assert discoverable is (condition == "on")
            recalled = [lesson["id"] for lesson in before if lesson["text"] in text]
            assert recalled == result["memory"]["first_request_recalled_ids"]
            phases.append(
                {
                    "phase": phase,
                    "run_id": result["run_id"],
                    "session_id": result["session_id"],
                    "phase_status": result["phase"],
                    "stop_reason": result["stop_reason"],
                    "independent_grade": grade["tests"],
                    "grade_passed": grade["passed"],
                    "before_grade": result["before_grade"]["tests"],
                    "default_answer_matches": result["default_answer_matches"],
                    "memory_capability_visible": capability_visible,
                    "memory_store_discoverable": discoverable,
                    "steps": result["steps"],
                    "turns": result["turns"],
                    "seconds": result["seconds"],
                    "usage": result["usage"],
                    "requests": len(result["requests"]),
                    "http_attempts": sum(row["http_attempts"] for row in result["requests"]),
                    "retries": sum(row["retries"] for row in result["requests"]),
                    "memory": {
                        key: result["memory"][key]
                        for key in (
                            "store_attempts",
                            "store_successes",
                            "discover_attempts",
                            "discover_successes",
                            "first_request_recalled_ids",
                            "run_recalled_ids",
                        )
                    },
                }
            )
        assert phases[0]["session_id"] != phases[1]["session_id"]
        final = load(HERE / condition / "phase2.json")
        lessons = []
        for lesson in final["memory"]["after"]["lessons"]:
            provenance = lesson["provenance"]
            source = next(
                event
                for event in all_events[provenance["run_id"]]
                if (
                    event["event"] == "tool_result"
                    and event["call"]["name"] == "read_file"
                    and provenance["evidence_id"]
                    in {event["hash"], event["outcome"].get("evidence_id")}
                )
            )
            assert source["outcome"]["status"] == "success"
            assert normalized(lesson["text"]) in normalized(source["outcome"]["content"])
            sources_current = all(
                sha256((HERE / condition / "candidate" / path).read_bytes()) == digest
                for path, digest in lesson["sources"].items()
            )
            lessons.append(
                {
                    "id": lesson["id"],
                    "text": lesson["text"],
                    "sources": lesson["sources"],
                    "provenance": provenance,
                    "source_verified": True,
                    "sources_current": sources_current,
                    "retrievals": lesson["retrievals"],
                }
            )
        if condition == "off":
            assert lessons == [] and all(phase["memory"]["store_attempts"] == 0 for phase in phases)
        conditions[condition] = {"phases": phases, "lessons": lessons}
    assert all(identity == identities[0] for identity in identities[1:])
    return {"independent_audit": "passed", "source_commit": COMMIT, "conditions": conditions}


if __name__ == "__main__":
    result = audit()
    (HERE / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"independent_audit": "passed", "phases": 4}))
