"""Offline budget, control, transport and identity checks; no live endpoint access."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "cache_benefit_driver", Path(__file__).with_name("run.py")
)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def test_fixed_schedule_has_six_identical_controls_then_abba_and_budget():
    rows = driver.schedule()
    assert len(rows) == driver.MAX_POSTS == 18
    assert {row["payload"] for row in rows[:6]} == {"calibration.json"}
    assert [row["delay"] for row in rows[:6]] == [0, 3, 3, 15, 3, 15]
    assert [(r["layout"], r["stage"]) for r in rows[6:]] == [
        ("A", 0),
        ("B", 0),
        ("B", 1),
        ("A", 1),
    ] * 3


def test_only_dynamic_message_moves_and_tool_pairs_remain_valid():
    system = [{"role": "system", "content": "Synthetic offline system."}]
    tools = [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object"},
        }
    ]
    a, b = [driver.build_payload(system, tools, "offline", 0, layout) for layout in ("A", "B")]
    driver.validate_payload_pair(a, b)
    assert a["tools"] == b["tools"] == tools
    changed = driver.build_payload(system, tools, "offline", 1, "B")
    assert 1024 < driver.token_prefix(b, changed)["shared_tokens"] < driver.MAX_INPUT
    b["tools"] = []
    with pytest.raises(ValueError, match="beyond input order"):
        driver.validate_payload_pair(a, b)


def test_positive_requires_a_valid_repeat_and_explicit_positive_usage():
    positive = {
        "status": "completed",
        "format_passed": True,
        "usage": {"cache_read_input_tokens": 1024},
    }
    zero = {"status": "completed", "format_passed": True, "usage": {"cache_read_input_tokens": 0}}
    assert not driver.is_positive([positive, zero])
    assert not driver.is_positive([zero, {**positive, "format_passed": False}])
    assert not driver.is_positive(
        [zero, {"status": "completed", "format_passed": True, "usage": {}}]
    )
    assert driver.is_positive([zero, positive])


async def test_one_post_no_retry_and_metadata_redaction():
    from aster.config import Settings

    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            503,
            headers={"x-request-id": "request-synthetic-secret", "set-cookie": "private"},
            json={
                "id": "response-id",
                "model": "synthetic-secret",
                "usage": {"input_tokens": 2000, "input_tokens_details": {"cached_tokens": 0}},
                "error": {"message": "hidden"},
            },
        )

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    record = await driver.send_once(
        {"model": "offline"},
        Settings(api_key="synthetic-secret", base_url="http://offline.invalid"),
        OfflineClient,
    )
    assert len(requests) == record["http_attempts"] == 1
    assert record["status"] == "failed" and record["usage"]["cache_read_input_tokens"] == 0
    assert record["raw_usage"]["input_tokens_details"]["cached_tokens"] == 0
    assert "synthetic-secret" not in json.dumps(record)
    assert "set-cookie" not in record["response_headers"]
    assert record["response_id"] == "response-id"


async def test_terminal_marker_no_tool_execution_and_unknown_cache_stays_unknown():
    from aster.config import Settings

    async def handle(_request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "offline-model",
                "id": "offline-id",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": driver.MARKER}],
                    }
                ],
                "usage": {"input_tokens": 2000, "output_tokens": 8},
            },
        )

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    result = await driver.send_once(
        {}, Settings(auth="none", base_url="http://offline.invalid"), OfflineClient
    )
    assert result["status"] == "completed" and result["format_passed"]
    assert "cache_read_input_tokens" not in result["usage"]
    assert result["response_model"] == "offline-model" and result["terminal_status"] == "completed"


@pytest.mark.parametrize("positive,expected", [(False, 6), (True, 18)])
async def test_control_gates_abba_and_refuses_another_attempt(
    tmp_path, monkeypatch, positive, expected
):
    monkeypatch.setattr(driver, "HERE", tmp_path)
    for name in ("run.py", "README.md", "test_driver.py"):
        (tmp_path / name).write_text("synthetic identity")
    rows = driver.schedule()
    payloads = tmp_path / "payloads"
    payloads.mkdir()
    body = driver.wire({"synthetic": True})
    identities = {}
    for row in rows:
        (payloads / row["payload"]).write_bytes(body)
        identities[row["payload"]] = {"sha256": driver.digest(body)}
    manifest = {
        "schedule": rows,
        "payloads": identities,
        "protocol_files": driver.protocol_hashes(),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    attempts, waits = [], []

    async def send(payload, settings):
        attempts.append(payload)
        return {
            "status": "completed",
            "format_passed": True,
            "http_attempts": 1,
            "usage": {"cache_read_input_tokens": 1024 if positive else 0},
        }

    async def sleep(seconds):
        waits.append(seconds)

    result = await driver.execute(manifest, None, sender=send, sleeper=sleep)
    assert len(attempts) == result["summary"]["http_attempts"] == expected
    assert result["summary"]["positive_control_established"] is positive
    assert waits[:5] == [3, 3, 15, 3, 15]
    assert result["status"] == ("completed" if positive else "stopped_no_positive_control")
    with pytest.raises(FileExistsError):
        await driver.execute(manifest, None, sender=send, sleeper=sleep)
    assert len(attempts) == expected


def test_summary_preserves_unknown_pair_and_rejects_invalid_marker():
    rows = [
        {
            "block": 1,
            "stage": 1,
            "layout": "A",
            "status": "completed",
            "format_passed": True,
            "usage": {},
        },
        {
            "block": 1,
            "stage": 1,
            "layout": "B",
            "status": "completed",
            "format_passed": False,
            "usage": {"cache_read_input_tokens": 20},
        },
    ]
    pair = driver.summary(rows, True)["layout_pairs"][0]
    assert pair["B_minus_A_cache_tokens"] is None and not pair["valid_pair"]


def test_prepare_product_templates_offline_with_fixed_token_budget(tmp_path, monkeypatch):
    from aster.config import Settings

    monkeypatch.setattr(driver, "HERE", tmp_path)
    for name in ("run.py", "README.md", "test_driver.py"):
        (tmp_path / name).write_text("offline protocol fixture")
    settings = Settings(
        auth="none",
        model=driver.MODEL,
        protocol="responses",
        output_tokens=256,
        context_tokens=128000,
        memory_enabled=False,
        max_workers=0,
        read_only=True,
        sandbox="required",
        approval="never",
    )
    monkeypatch.setattr(driver, "settings_for", lambda env: settings)
    driver.prepare({"commit": "offline-fixture"}, tmp_path / "unused.env")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["payloads"]) == 13 and len(manifest["schedule"]) == 18
    assert all(
        1024 < item["local_tokens"] <= driver.MAX_INPUT for item in manifest["payloads"].values()
    )
    for block in manifest["prefix_proxies"].values():
        assert block["B"]["shared_tokens"] > block["A"]["shared_tokens"]
    with pytest.raises(ValueError, match="replace"):
        driver.prepare({"commit": "offline-fixture"}, tmp_path / "unused.env")


async def test_http_timeout_has_one_attempt_and_no_exception_body(tmp_path, monkeypatch):
    from aster.config import Settings

    monkeypatch.setattr(driver, "TIMEOUT", 0.005)

    async def handle(_request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    result = await driver.send_once(
        {}, Settings(auth="none", base_url="http://offline.invalid"), OfflineClient
    )
    assert result["http_attempts"] == 1 and result["error"] == "TimeoutError"
    assert "offline.invalid" not in json.dumps(result)
