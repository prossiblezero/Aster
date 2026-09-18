"""Independent, offline verification of the six saved cache-probe records."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMITS = {
    "old": "59ca274f69665100dc20e3ef7343d009a84e3c35",
    "new": "95178d9931c28e2333fc0a093f8c313ea1668c2c",
}


def load(path):
    return json.loads(path.read_text())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def check():
    experiment = load(HERE / "experiment.json")
    assert experiment["order"] == ["old"] * 3 + ["new"] * 3
    results, manifests = {}, {}
    for condition, commit in COMMITS.items():
        manifest_path = HERE / condition / "manifest.json"
        assert sha256(manifest_path.read_bytes()) == experiment["manifest_sha256"][condition]
        manifest = manifests[condition] = load(manifest_path)
        result = load(HERE / condition / "result.json")
        assert manifest["source_commit"] == result["source_commit"] == commit
        assert result["source_unchanged"] and result["experiment_unchanged"]
        assert len(result["requests"]) == len(manifest["requests"]) == 3
        assert not [
            path for path in (HERE / condition / "workspace").iterdir() if path.name != ".aster"
        ]
        for name, digest in manifest["experiment_files"].items():
            assert sha256((HERE / name).read_bytes()) == digest
        writes = []
        for index, record in enumerate(result["requests"], 1):
            payload = load(HERE / condition / "payloads" / f"{index}.json")
            wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            assert (
                sha256(wire)
                == record["payload_sha256"]
                == manifest["requests"][index - 1]["payload_sha256"]
            )
            assert payload["model"] == "gpt-5.6-sol" and payload["max_output_tokens"] == 32768
            assert "stream" not in payload and len(payload["input"]) == 3
            assert record["index"] == index and record["http_attempts"] == 1
            assert record["status"] == "completed" and record["http_status"] == 200
            assert record["text"].strip() == "CACHE_PROBE_OK" and record["tool_call_count"] == 0
            assert record["seconds"] < 300
            raw = record["raw_usage"]
            assert record["usage"]["input_tokens"] == raw["input_tokens"]
            assert (
                record["usage"]["cache_read_input_tokens"]
                == raw["input_tokens_details"]["cached_tokens"]
            )
            writes.append(raw["input_tokens_details"].get("cache_write_tokens"))
        results[condition] = {
            "source_commit": commit,
            "source_sha256": manifest["source"]["sha256"],
            "prefix_proxy": manifest["prefix"],
            "format_passed": 3,
            "http_attempts": 3,
            "retries": 0,
            "request_seconds": [row["seconds"] for row in result["requests"]],
            "usage": result["summary"]["usage"],
            "request_input_tokens": [row["usage"]["input_tokens"] for row in result["requests"]],
            "request_cache_read_tokens": [
                row["usage"]["cache_read_input_tokens"] for row in result["requests"]
            ],
            "raw_cache_write_tokens": writes,
            "model_seconds": sum(row["seconds"] for row in result["requests"]),
        }
    for key in ("configuration", "connection_sha256", "experiment_files", "task_sha256"):
        assert manifests["old"][key] == manifests["new"][key]
    assert [row["control"] for row in manifests["old"]["requests"]] == [
        row["control"] for row in manifests["new"]["requests"]
    ]
    return {"independent_audit": "passed", "conditions": results}


if __name__ == "__main__":
    result = check()
    (HERE / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"independent_audit": "passed", "requests": 6, "format_passed": 6}))
