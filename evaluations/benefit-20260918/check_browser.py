"""Independent browser acceptance against the public Campus Planner contracts.

No model calls and no candidate-internal functions. Candidate files are served
read-only; browser state, downloads, screenshots and results live outside them.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
STORAGE_KEY = "campus-planner-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ERROR_TEXT = re.compile(
    r"失败|错误|无效|非法|损坏|无法|不能|不支持|超过|超出|重复|未保存|不可用|不合法"
)


def task(ident="one", title="高等数学练习", course="数学", due="2099-10-08T23:59", **extra):
    return {
        "id": ident,
        "title": title,
        "course": course,
        "due": due,
        "notes": "课后练习",
        "completed": False,
        **extra,
    }


def package(tasks):
    return {"version": 1, "tasks": tasks}


def escape_ics_text(value):
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def parse_ics(data):
    """Validate physical serialization before unfolding and parsing properties."""
    assert isinstance(data, bytes), "ICS must be downloaded as bytes"
    data.decode("utf-8", errors="strict")
    assert data.endswith(b"\r\n"), "ICS must end with CRLF"
    assert b"\n" not in data.replace(b"\r\n", b""), "bare LF in ICS"
    assert b"\r" not in data.replace(b"\r\n", b""), "bare CR in ICS"
    logical = []
    for line in data.split(b"\r\n")[:-1]:
        assert len(line) <= 75, "physical ICS line exceeds 75 UTF-8 bytes"
        decoded = line.decode("utf-8", errors="strict")
        assert not decoded.startswith("\t"), "continuations must begin with a space"
        if decoded.startswith(" "):
            assert logical, "continuation without preceding line"
            logical[-1] += decoded[1:]
        else:
            logical.append(decoded)
    assert logical[0] == "BEGIN:VCALENDAR" and logical[-1] == "END:VCALENDAR"
    calendar, events, current = {}, [], None
    for line in logical[1:-1]:
        if line == "BEGIN:VEVENT":
            assert current is None, "nested VEVENT"
            current = {}
        elif line == "END:VEVENT":
            assert current is not None, "unmatched END:VEVENT"
            events.append(current)
            current = None
        else:
            assert ":" in line, f"invalid ICS property: {line[:80]}"
            key, value = line.split(":", 1)
            destination = calendar if current is None else current
            assert key not in destination, f"duplicate property {key}"
            destination[key] = value
    assert current is None, "unterminated VEVENT"
    assert calendar.get("VERSION") == "2.0", "missing VERSION:2.0"
    assert calendar.get("PRODID"), "missing PRODID"
    return events


def verify_calendar(data, tasks, *, earliest=None, latest=None):
    events = parse_ics(data)
    expected = {
        item["id"] + "@campus-planner.local": item for item in tasks if not item["completed"]
    }
    actual = {event.get("UID"): event for event in events}
    assert len(actual) == len(events), "duplicate event UID"
    assert actual.keys() == expected.keys(), "calendar must contain exactly the unfinished tasks"
    for uid, item in expected.items():
        event = actual[uid]
        start = datetime.strptime(item["due"], "%Y-%m-%dT%H:%M").replace(tzinfo=SHANGHAI)
        assert event.get("DTSTART") == start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        assert event.get("DTEND") == (start + timedelta(minutes=30)).astimezone(UTC).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        assert event.get("SUMMARY") == escape_ics_text(item["course"] + "：" + item["title"])
        assert event.get("DESCRIPTION") == escape_ics_text(item["notes"])
        stamp = event.get("DTSTAMP", "")
        assert re.fullmatch(r"\d{8}T\d{6}Z", stamp), "DTSTAMP must be UTC basic format"
        instant = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if earliest is not None:
            assert instant >= earliest - timedelta(seconds=5), "DTSTAMP predates export"
        if latest is not None:
            assert instant <= latest + timedelta(seconds=5), "DTSTAMP is in the future"
    return actual


class ReadOnlyHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def list_directory(self, _path):
        self.send_error(403, "Directory listing disabled")
        return None

    def send_head(self):
        target = Path(self.translate_path(self.path))
        root = Path(self.directory).resolve()
        if not target.resolve().is_relative_to(root) or any(
            part.startswith(".") for part in target.relative_to(root).parts
        ):
            self.send_error(403, "Outside public workspace")
            return None
        return super().send_head()


@contextlib.contextmanager
def static_server(workspace):
    handler = functools.partial(ReadOnlyHandler, directory=str(workspace))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def storage(page):
    raw = page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(raw) if raw is not None else None


def cards(page):
    return page.get_by_test_id("task-list").get_by_test_id("task-card")


def card(page, title):
    return cards(page).filter(has_text=title)


def add(page, title="线性代数练习", course="数学", due="2099-11-08T23:59", notes="矩阵与行列式"):
    for label, value in (("作业名称", title), ("课程", course), ("截止时间", due), ("备注", notes)):
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("button", name="添加作业", exact=True).click()


def visible_error(page, pattern=ERROR_TEXT):
    assert pattern.search(page.locator("body").inner_text()), (
        "missing understandable error feedback"
    )


class BrowserAudit:
    def __init__(self, browser, origin, output):
        self.browser, self.origin = browser, origin
        self.artifacts = HERE / "screenshots" / output.stem
        if self.artifacts.exists():
            raise ValueError(f"Refusing to overwrite browser artifacts: {self.artifacts}")
        self.artifacts.mkdir(parents=True)
        self.cases, self.screenshots, self.downloads, self.external_requests = [], [], [], []

    def run(
        self,
        name,
        check,
        *,
        tasks=None,
        raw=None,
        mobile=False,
        broken_writes=False,
        screenshot=False,
    ):
        errors = []
        context = self.browser.new_context(
            viewport={"width": 390, "height": 844} if mobile else {"width": 1365, "height": 900},
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
            accept_downloads=True,
            service_workers="block",
        )

        def route(request):
            if request.request.url.startswith(self.origin + "/"):
                request.continue_()
            else:
                self.external_requests.append({"case": name, "url": request.request.url[:300]})
                request.abort()

        context.route("**/*", route)
        if hasattr(context, "route_web_socket"):

            def deny_socket(socket):
                self.external_requests.append({"case": name, "url": socket.url[:300]})
                socket.close()

            context.route_web_socket("**/*", deny_socket)
        fixture = (
            raw
            if raw is not None
            else json.dumps(package(tasks), ensure_ascii=False)
            if tasks is not None
            else None
        )
        # Seed only once per tab. Reload must test the candidate's actual writes.
        quota_script = (
            "Storage.prototype.setItem = function() { throw new DOMException('Evaluation quota', 'QuotaExceededError'); };"
            if broken_writes
            else ""
        )
        context.add_init_script(f"""(() => {{
            const fixture = {json.dumps(fixture)};
            if (!sessionStorage.getItem('__evaluation_seeded')) {{
                if (fixture !== null) localStorage.setItem({json.dumps(STORAGE_KEY)}, fixture);
                sessionStorage.setItem('__evaluation_seeded', '1');
            }}
            window.__evaluationXss = false;
            {quota_script}
        }})();""")
        page = context.new_page()
        page.set_default_timeout(2500)
        page.on("pageerror", lambda error: errors.append(str(error)[:1000]))
        page.on("dialog", lambda dialog: dialog.accept())
        result = {"name": name, "passed": False}
        try:
            page.goto(self.origin + "/", wait_until="networkidle", timeout=10000)
            page.get_by_test_id("task-list").wait_for(state="attached")
            check(page)
            assert not errors, "uncaught browser errors: " + "; ".join(errors)
            result["passed"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"[:2500]
        finally:
            if screenshot or not result["passed"]:
                path = self.artifacts / (name + ".png")
                try:
                    page.screenshot(path=str(path), full_page=True, timeout=5000)
                    self.screenshots.append(str(path.relative_to(HERE)))
                    result["screenshot"] = str(path.relative_to(HERE))
                except Exception as exc:
                    result["screenshot_error"] = type(exc).__name__
            result["browser_errors"] = errors
            self.cases.append(result)
            context.close()

    def download(self, page, button, filename):
        with page.expect_download() as received:
            page.get_by_role("button", name=button, exact=True).click()
        download = received.value
        assert download.suggested_filename == filename, "download filename violates public contract"
        path = self.artifacts / f"download-{len(self.downloads) + 1}-{filename}"
        download.save_as(str(path))
        self.downloads.append(str(path.relative_to(HERE)))
        return path.read_bytes()


def check_base(audit):
    def empty(page):
        assert cards(page).count() == 0, "first use must be empty"
        assert page.get_by_role("button", name="添加作业", exact=True).is_visible()
        assert page.locator("body").inner_text().strip()

    audit.run("base-empty-desktop", empty, screenshot=True)

    def crud(page):
        add(page)
        row = card(page, "线性代数练习")
        assert row.count() == 1
        original = storage(page)["tasks"][0]
        assert original["title"] == "线性代数练习" and original["course"] == "数学"
        assert original["due"] == "2099-11-08T23:59" and original["notes"] == "矩阵与行列式"
        assert original["id"] and original["completed"] is False
        row.get_by_role("button", name="编辑", exact=True).click()
        assert page.get_by_label("截止时间", exact=True).input_value() == original["due"]
        page.get_by_label("作业名称", exact=True).fill("修改后的矩阵练习")
        page.get_by_label("课程", exact=True).fill("应用数学")
        page.get_by_label("截止时间", exact=True).fill("2099-12-01T09:30")
        page.get_by_label("备注", exact=True).fill("修改后的备注")
        page.get_by_role("button", name="保存修改", exact=True).click()
        assert storage(page)["tasks"][0]["id"] == original["id"], "editing changed stable ID"
        updated = storage(page)["tasks"][0]
        assert updated["course"] == "应用数学" and updated["due"] == "2099-12-01T09:30"
        assert updated["notes"] == "修改后的备注"
        courses = page.get_by_label("筛选课程", exact=True)
        assert courses.get_by_role("option", name="应用数学", exact=True).count() == 1
        assert courses.get_by_role("option", name="数学", exact=True).count() == 0
        row = card(page, "修改后的矩阵练习")
        row.get_by_role("button", name="完成", exact=True).click()
        assert storage(page)["tasks"][0]["completed"] is True
        row.get_by_role("button", name="恢复", exact=True).click()
        assert storage(page)["tasks"][0]["completed"] is False
        page.reload(wait_until="networkidle")
        assert card(page, "修改后的矩阵练习").count() == 1
        assert storage(page)["tasks"][0]["id"] == original["id"]
        observed = []
        page.on("dialog", lambda dialog: observed.append(dialog.type))
        card(page, "修改后的矩阵练习").get_by_role("button", name="删除", exact=True).click()
        if not observed:
            confirmation = page.get_by_role("dialog")
            assert confirmation.is_visible(), "deletion needs confirmation"
            confirmation.get_by_role("button", name=re.compile("确认|确定|删除")).last.click()
        assert cards(page).count() == 0 and storage(page)["tasks"] == []

    audit.run("base-create-edit-complete-restore-delete-persist", crud)

    def whitespace(page):
        add(page, title="   ", course="  ")
        assert cards(page).count() == 0
        value = storage(page)
        assert value is None or value["tasks"] == []

    audit.run("base-reject-blank-required-fields", whitespace)

    fixtures = [
        task("late", "过期作业", due="2001-01-01T10:00"),
        task("first", "先到的同刻任务", course="物理", due="2099-01-01T10:00", notes="量子关键词"),
        task("second", "后到的同刻任务", course="物理", due="2099-01-01T10:00"),
        task("done", "完成的旧作业", due="2000-01-01T10:00", completed=True),
    ]

    def sorting(page):
        titles = ["完成的旧作业", "过期作业", "先到的同刻任务", "后到的同刻任务"]
        assert cards(page).count() == 4
        for index, title in enumerate(titles):
            assert title in cards(page).nth(index).inner_text(), (
                "deadline sorting or stable tie order"
            )
        assert "逾期" in card(page, "过期作业").inner_text()
        assert "逾期" not in card(page, "完成的旧作业").inner_text()
        body = page.locator("body").inner_text()
        assert all(word in body for word in ("未完成", "已完成", "逾期")), (
            "missing status summaries"
        )

    audit.run("base-sort-stable-ties-and-overdue", sorting, tasks=fixtures, screenshot=True)

    def filters(page):
        from playwright.sync_api import expect

        search = page.get_by_label("搜索作业", exact=True)
        for text, count in (("量子关键词", 1), ("物理", 2), ("后到", 1)):
            search.fill(text)
            expect(cards(page)).to_have_count(count)
        search.fill("")
        page.get_by_label("筛选课程", exact=True).select_option(label="物理")
        expect(cards(page)).to_have_count(2)
        page.get_by_label("筛选状态", exact=True).select_option(label="未完成")
        search.fill("量子关键词")
        expect(cards(page)).to_have_count(1)
        page.get_by_label("筛选状态", exact=True).select_option(label="已完成")
        expect(cards(page)).to_have_count(0)

    audit.run("base-combined-search-and-filters", filters, tasks=fixtures)

    def mobile(page):
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), (
            "horizontal mobile overflow"
        )
        add(page, title="手机新增作业")
        assert card(page, "手机新增作业").count() == 1
        for label in ("作业名称", "课程", "截止时间", "备注", "搜索作业"):
            assert page.get_by_label(label, exact=True).is_visible()

    audit.run("base-mobile", mobile, mobile=True, screenshot=True)

    def examples(page):
        page.get_by_role("button", name="加载示例", exact=True).click()
        assert cards(page).count() > 0
        add(page, title="需要保留的个人作业")
        dialogs = []
        page.remove_all_listeners("dialog")
        page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
        page.get_by_role("button", name="加载示例", exact=True).click()
        # A confirmation dialog or non-destructive append/refusal is acceptable.
        assert any(t["title"] == "需要保留的个人作业" for t in storage(page)["tasks"]), (
            "examples silently replaced existing data"
        )

    audit.run("base-example-loading-preserves-existing-work", examples)

    def damaged(page):
        visible_error(page)
        assert page.get_by_role("button", name="添加作业", exact=True).is_visible()

    for label, raw in (
        ("json", "{broken"),
        ("version", json.dumps({"version": 99, "tasks": []})),
        ("record", json.dumps(package([task(completed="false")]))),
    ):
        audit.run("base-corrupt-storage-" + label, damaged, raw=raw)

    def quota(page):
        add(page, title="保存失败的作业")
        visible_error(
            page,
            re.compile(
                "未保存|保存失败|无法保存|不能保存|未能保存|存储.*(?:失败|不可用|不足)|空间不足"
            ),
        )
        assert storage(page) is None, "failed write was reported as durable"

    audit.run("base-storage-write-failure", quota, broken_writes=True)


def import_backup(page, value, *, raw=None):
    data = raw if raw is not None else json.dumps(value, ensure_ascii=False).encode()
    before = {
        "text": page.locator("body").inner_text(),
        "stored": page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY),
    }
    page.get_by_label("导入备份", exact=True).set_input_files(
        {"name": "import.json", "mimeType": "application/json", "buffer": data}
    )
    # Wait for actual FileReader effects or visible success/error feedback.
    page.wait_for_function(
        "before => document.body.innerText !== before.text || localStorage.getItem('campus-planner-v1') !== before.stored",
        arg=before,
    )


def check_backup(audit):
    fixtures = [task("same", "旧任务"), task("keep", "必须保留的任务", course="历史")]

    def export(page):
        before = storage(page)
        data = audit.download(page, "导出备份", "campus-planner-backup.json")
        assert json.loads(data.decode("utf-8")) == before
        assert storage(page) == before

    audit.run("backup-download", export, tasks=fixtures)

    def merge(page):
        incoming = [task("same", "同ID更新", completed=True), task("new", "新增任务")]
        import_backup(page, package(incoming))
        actual = storage(page)["tasks"]
        assert [x["id"] for x in actual] == ["same", "keep", "new"]
        assert actual == [incoming[0], fixtures[1], incoming[1]]
        page.reload(wait_until="networkidle")
        assert cards(page).count() == 3

    audit.run("backup-merge-replace-append-preserve", merge, tasks=fixtures)

    def empty(page):
        before = storage(page)
        import_backup(page, package([]))
        assert storage(page) == before

    audit.run("backup-empty-package-no-change", empty, tasks=fixtures)

    def exact_limit(page):
        incoming = task("limit", "恰好一MiB备份")
        incoming["extra"] = ""
        data = json.dumps(package([incoming]), ensure_ascii=False).encode()
        incoming["extra"] = "x" * (1024 * 1024 - len(data))
        data = json.dumps(package([incoming]), ensure_ascii=False).encode()
        assert len(data) == 1024 * 1024
        import_backup(page, None, raw=data)
        assert storage(page)["tasks"] == [
            {key: value for key, value in incoming.items() if key != "extra"}
        ]

    audit.run("backup-exact-one-mib-is-accepted", exact_limit, tasks=[])

    invalid = {
        "json": b"{broken",
        "version": json.dumps({"version": 2, "tasks": []}).encode(),
        "duplicate-id": json.dumps(package([task("dup"), task("dup")])).encode(),
        "partial-invalid": json.dumps(
            package([task("new", "不能部分加入"), task("bad", completed="false")])
        ).encode(),
        "invalid-date": json.dumps(package([task("bad", due="2026-02-30T10:00")])).encode(),
        "oversize": json.dumps(package([task("big", notes="x" * (1024 * 1024))])).encode(),
    }
    for label, data in invalid.items():

        def reject(page, data=data):
            before = page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
            import_backup(page, None, raw=data)
            assert page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY) == before
            assert cards(page).count() == 2
            visible_error(page)

        audit.run("backup-reject-" + label, reject, tasks=fixtures)

    def xss(page):
        title = '<img src=x onerror="window.__evaluationXss=true">'
        incoming = task("unsafe", title, notes="<script>window.__evaluationXss=true</script>")
        incoming["extra"] = "ignored field"
        import_backup(page, package([incoming]))
        assert page.evaluate("window.__evaluationXss") is False
        assert title in cards(page).inner_text()
        assert cards(page).locator("img,script").count() == 0
        saved = next(t for t in storage(page)["tasks"] if t["id"] == "unsafe")
        assert "extra" not in saved, "extra backup fields must be ignored"

    audit.run("backup-plain-text-and-extra-fields", xss, tasks=[])


def check_calendar(audit):
    fixtures = [
        task(
            "before-midnight",
            "转义,分号;与\\路径",
            course="数学,专题;\\",
            due="2026-10-08T07:45",
            notes="第一行\n第二行,分号;与\\路径",
        ),
        task("local-midnight", "跨本地午夜", due="2026-10-08T23:50"),
        task("unicode", "多字节折行" * 30, course="中文课程" * 20, notes="中文备注" * 50),
        task("completed", "不应导出", completed=True),
    ]

    def interoperability(page):
        before, earliest = storage(page), datetime.now(UTC)
        data = audit.download(page, "导出日历", "campus-planner.ics")
        first = verify_calendar(data, fixtures, earliest=earliest, latest=datetime.now(UTC))
        assert b"\r\n " in data, "long multilingual fields need folding"
        second = verify_calendar(audit.download(page, "导出日历", "campus-planner.ics"), fixtures)
        assert first.keys() == second.keys(), "UID changes across repeated exports"
        assert storage(page) == before

    audit.run("calendar-utc-duration-uid-escape-byte-folding", interoperability, tasks=fixtures)
    for label, data in (("empty", []), ("all-completed", [task(completed=True)])):

        def empty(page, data=data):
            verify_calendar(audit.download(page, "导出日历", "campus-planner.ics"), data)

        audit.run("calendar-" + label, empty, tasks=data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--features", default="base")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace, output = args.workspace.resolve(), args.output.resolve()
    features = list(dict.fromkeys(args.features.split(",")))
    if not features or any(name not in {"base", "backup", "calendar"} for name in features):
        parser.error("features must be a comma-separated subset of base,backup,calendar")
    if not workspace.is_dir() or not (workspace / "index.html").is_file():
        parser.error("workspace must contain the candidate index.html")
    if not output.is_relative_to(HERE) or output.is_relative_to(workspace) or output.exists():
        parser.error(
            "output must be a new path under this evaluation directory, outside the candidate"
        )
    result = {
        "workspace": str(workspace),
        "features": features,
        "cases": [],
        "errors": [],
        "screenshots": [],
    }
    try:
        from playwright.sync_api import sync_playwright

        with static_server(workspace) as origin, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                audit = BrowserAudit(browser, origin, output)
                for feature in features:
                    {"base": check_base, "backup": check_backup, "calendar": check_calendar}[
                        feature
                    ](audit)
                audit.cases.append(
                    {
                        "name": "no-external-network",
                        "passed": not audit.external_requests,
                        "requests": audit.external_requests,
                    }
                )
                result.update(
                    cases=audit.cases,
                    screenshots=audit.screenshots,
                    downloads=audit.downloads,
                    errors=[
                        {"case": item["name"], "error": item.get("error", "external request")}
                        for item in audit.cases
                        if not item["passed"]
                    ],
                )
            finally:
                browser.close()
    except Exception as exc:
        result["errors"].append({"infrastructure": f"{type(exc).__name__}: {exc}"[:2000]})
    result["passed"] = (
        bool(result["cases"])
        and not result["errors"]
        and all(item["passed"] for item in result["cases"])
    )
    result["totals"] = {
        "passed": sum(item["passed"] for item in result["cases"]),
        "executed": len(result["cases"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": result["passed"], "totals": result["totals"], "output": str(output)},
            ensure_ascii=False,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
