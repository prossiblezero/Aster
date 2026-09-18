"""Offline tests of the independent ICS oracle; these are not product evaluations."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "campus_browser_check", Path(__file__).with_name("check_browser.py")
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def folded(lines):
    physical = []
    for line in lines:
        current = ""
        for char in line:
            if len((current + char).encode()) > 75:
                physical.append(current)
                current = " "
            current += char
        physical.append(current)
    return ("\r\n".join(physical) + "\r\n").encode()


def sample():
    item = checker.task(
        "abc",
        "多字节标题" * 20,
        course="数学",
        due="2026-10-08T07:45",
        notes="反\\斜线,分号;\n第二行",
    )
    data = folded(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Independent grader fixture//EN",
            "BEGIN:VEVENT",
            "UID:abc@campus-planner.local",
            "DTSTAMP:20260918T100000Z",
            "DTSTART:20261007T234500Z",
            "DTEND:20261008T001500Z",
            "SUMMARY:数学：" + "多字节标题" * 20,
            "DESCRIPTION:反\\\\斜线\\,分号\\;\\n第二行",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    return item, data


def test_valid_unicode_folded_calendar_and_utc_midnight():
    item, data = sample()
    stamp = datetime(2026, 9, 18, 10, tzinfo=UTC)
    assert checker.verify_calendar(data, [item], earliest=stamp, latest=stamp).keys() == {
        "abc@campus-planner.local"
    }


@pytest.mark.parametrize(
    "before,after",
    [
        (b"\r\n", b"\n"),
        (b"DTSTART:20261007T234500Z", b"DTSTART:20261008T074500Z"),
        (b"DTEND:20261008T001500Z", b"DTEND:20261008T004500Z"),
        (b"UID:abc@campus-planner.local", b"UID:unstable@campus-planner.local"),
        (b"\\,", b","),
    ],
)
def test_wrong_serialization_time_duration_identity_and_escaping_rejected(before, after):
    item, data = sample()
    with pytest.raises(AssertionError):
        checker.verify_calendar(data.replace(before, after), [item])


def test_character_count_folding_is_not_utf8_byte_folding():
    item, data = sample()
    unfolded = data.replace(b"\r\n ", b"")
    assert len("SUMMARY:数学：" + "多字节标题" * 20) > 75
    with pytest.raises(AssertionError, match="75 UTF-8 bytes"):
        checker.verify_calendar(unfolded, [item])


def test_valid_empty_calendar_and_unexpected_completed_event():
    data = folded(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:grader-fixture", "END:VCALENDAR"])
    assert checker.verify_calendar(data, []) == {}
    item, data = sample()
    with pytest.raises(AssertionError, match="exactly the unfinished"):
        checker.verify_calendar(data, [{**item, "completed": True}])


def test_browser_harness_preserves_reload_state_and_continues_after_failure(tmp_path, monkeypatch):
    """A blank infrastructure fixture, never a substitute product acceptance."""
    from playwright.sync_api import sync_playwright

    workspace = tmp_path / "static"
    workspace.mkdir()
    (workspace / "index.html").write_text(
        '<html><body><div data-testid="task-list"></div></body></html>'
    )
    monkeypatch.setattr(checker, "HERE", tmp_path)

    def fail(_page):
        raise AssertionError("deliberate oracle negative control")

    def reload(page):
        assert checker.storage(page) == checker.package([])
        value = checker.package([checker.task()])
        page.evaluate(
            "value => localStorage.setItem('campus-planner-v1', JSON.stringify(value))", value
        )
        page.reload(wait_until="networkidle")
        assert checker.storage(page) == value, "init script must not overwrite writes on reload"

    with checker.static_server(workspace) as origin, sync_playwright() as playwright:
        with playwright.chromium.launch(headless=True) as browser:
            audit = checker.BrowserAudit(browser, origin, tmp_path / "infrastructure.json")
            audit.run("deliberate-failure", fail)
            audit.run("reload-state", reload, tasks=[])
    assert [case["passed"] for case in audit.cases] == [False, True]
    assert audit.cases[0]["screenshot"]
