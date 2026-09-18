"""Read-only verification of saved requests, measurements and the predeclared experiment."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("cache_probe", Path(__file__).with_name("run.py"))
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def check():
    manifest = json.loads((driver.HERE / "manifest.json").read_text())
    results = json.loads((driver.HERE / "results.json").read_text())
    assert driver.digest((driver.HERE / "manifest.json").read_bytes()) == results["manifest_sha256"]
    assert driver.protocol_hashes() == manifest["protocol_files"]
    assert results["protocol_unchanged"]
    assert manifest["schedule"] == driver.schedule()
    with driver.frozen_product(manifest["source"]["commit"]) as source:
        from aster.provider import ModelClient

        assert source == manifest["source"]
        payloads = {}
        for name, metadata in manifest["payloads"].items():
            body = (driver.HERE / "payloads" / name).read_bytes()
            assert driver.digest(body) == metadata["sha256"]
            payload = payloads[name] = json.loads(body)
            assert driver.wire(payload) == body
            assert driver.token_prefix(payload, payload)["first_tokens"] == metadata["local_tokens"]
            assert 1024 < metadata["local_tokens"] <= driver.MAX_INPUT
            assert payload["model"] == driver.MODEL
            assert payload["max_output_tokens"] == driver.OUTPUT_TOKENS
        for block in range(1, 4):
            for stage in (0, 1):
                driver.validate_payload_pair(
                    payloads[f"block-{block}-A{stage}.json"],
                    payloads[f"block-{block}-B{stage}.json"],
                )
        records = results["records"]
        assert 6 <= len(records) <= driver.MAX_POSTS
        assert len({row["payload_sha256"] for row in records[:6]}) == 1
        for planned, row in zip(manifest["schedule"], records, strict=False):
            assert all(row[key] == value for key, value in planned.items())
            assert row["payload_sha256"] == manifest["payloads"][row["payload"]]["sha256"]
            assert row["http_attempts"] == 1
            assert row["usage"] == ModelClient._usage(row["raw_usage"], "responses")
            assert set(row.get("response_headers", {})) <= driver.HEADERS
            if row["actual_idle_seconds"] is not None:
                assert row["actual_idle_seconds"] >= row["delay"] - 0.01
            if row.get("format_passed"):
                assert row["text"].strip() == driver.MARKER and row["tool_call_count"] == 0
        positive = driver.is_positive(records[:6])
        assert results["summary"] == driver.summary(records, positive)
        assert len(records) == (18 if positive else 6)
        assert results["status"] == ("completed" if positive else "stopped_no_positive_control")
    return {"audit": "passed", "requests": len(records), "positive_control_established": positive}


if __name__ == "__main__":
    print(json.dumps(check()))
