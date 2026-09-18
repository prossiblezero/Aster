"""Public-UI acceptance for the frozen study planner; no candidate imports or model calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_PATH = HERE.parent / "benefit-20260918/check_browser.py"
START = "2099-06-01"
COURSES = [
    {"name": "高等数学", "exam": "2099-06-04", "hours": "5"},
    {"name": "大学英语", "exam": "2099-06-05", "hours": "3"},
    {"name": "计算机基础", "exam": "2099-06-07", "hours": "2.5"},
]


def decimal_hours(text):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:小时)?\s*", str(text))
    assert match, f"Invalid visible hours: {text!r}"
    try:
        value = Decimal(match[1])
    except InvalidOperation as exc:
        raise AssertionError("Invalid hours") from exc
    assert value.is_finite(), "Hours must be finite"
    return value


def required_shortage(courses, start, cap):
    """Independent deadline-prefix bound; no expected per-day allocation is constructed."""
    start, cap = date.fromisoformat(start), decimal_hours(cap)
    assert cap > 0
    by_deadline = defaultdict(Decimal)
    for course in courses:
        by_deadline[date.fromisoformat(course["exam"])] += decimal_hours(course["hours"])
    demand, shortage = Decimal(0), Decimal(0)
    for deadline in sorted(by_deadline):
        demand += by_deadline[deadline]
        capacity = max(0, (deadline - start).days) * cap
        shortage = max(shortage, demand - capacity)
    return shortage


def verify_schedule(rows, courses, start, cap):
    """Allow any complete/maximal schedule satisfying public dates and hour constraints."""
    start_date, cap = date.fromisoformat(start), decimal_hours(cap)
    by_name = {course["name"]: course for course in courses}
    assert len(by_name) == len(courses), "Oracle fixtures require unique course names"
    daily, totals = defaultdict(Decimal), defaultdict(Decimal)
    for row in rows:
        assert set(row) >= {"日期", "课程", "小时"}, "Missing plan columns"
        name = row["课程"].strip()
        assert name in by_name, f"Unknown/deleted course still scheduled: {name}"
        day = date.fromisoformat(row["日期"].strip())
        assert start_date <= day < date.fromisoformat(by_name[name]["exam"]), (
            f"Schedule outside {name}'s allowed dates: {day}"
        )
        hours = decimal_hours(row["小时"])
        assert hours > 0, "Each schedule row must contain positive hours"
        daily[day] += hours
        totals[name] += hours
    assert all(value <= cap for value in daily.values()), "Daily total exceeds limit"
    for name, course in by_name.items():
        assert totals[name] <= decimal_hours(course["hours"]), f"Too many hours for {name}"
    requested = sum((decimal_hours(course["hours"]) for course in courses), Decimal(0))
    shortage = required_shortage(courses, start, str(cap))
    assert sum(totals.values(), Decimal(0)) == requested - shortage, (
        "Missing feasible study hours or incorrect shortage allocation"
    )
    if not shortage:
        assert all(
            totals[name] == decimal_hours(course["hours"]) for name, course in by_name.items()
        )
    return shortage


def table_records(page, caption):
    table = page.get_by_role("table", name=caption, exact=True)
    table.wait_for(state="visible")
    headers = table.get_by_role("columnheader").all_text_contents()
    headers = [header.strip() for header in headers]
    result = []
    for row in table.get_by_role("row").all():
        if not row.locator("td").count():
            continue
        cells = row.locator("td, th")
        if cells.count() == 1 and int(cells.first.get_attribute("colspan") or "1") > 1:
            continue  # A public empty-state row, not a fabricated scheduled entry.
        assert len(headers) == cells.count(), f"Inconsistent {caption} table structure"
        result.append(
            dict(zip(headers, [value.strip() for value in cells.all_text_contents()], strict=True))
        )
    return result


def plan_rows(page):
    return table_records(page, "复习计划")


def course_row(page, name):
    return (
        page.get_by_role("table", name="课程列表", exact=True)
        .get_by_role("row")
        .filter(has=page.get_by_text(name, exact=True))
    )


def set_settings(page, start, cap):
    page.get_by_label("开始日期", exact=True).fill(start)
    page.get_by_label("每日上限（小时）", exact=True).fill(str(cap))


def add_course(page, course):
    page.get_by_label("课程名称", exact=True).fill(course["name"])
    page.get_by_label("考试日期", exact=True).fill(course["exam"])
    page.get_by_label("剩余小时", exact=True).fill(str(course["hours"]))
    page.get_by_role("button", name="添加课程", exact=True).click()
    row = course_row(page, course["name"])
    row.wait_for(state="visible")
    assert course["exam"] in row.inner_text(), "Course list omits exam date"


def generate(page, courses, start, cap):
    page.get_by_role("button", name="生成计划", exact=True).click()
    table = page.get_by_role("table", name="复习计划", exact=True)
    rows = [] if not courses and not table.is_visible() else plan_rows(page)
    shortage = verify_schedule(rows, courses, start, cap)
    status = "\n".join(page.get_by_role("status").all_text_contents())
    if shortage:
        assert "不足" in status, "Missing visible capacity-shortage warning"
        numbers = [Decimal(number) for number in re.findall(r"\d+(?:\.\d+)?", status)]
        assert shortage in numbers, f"Warning does not show missing {shortage} hours"
    else:
        assert "不足" not in status, "Stale shortage warning after feasible schedule"
    return rows


def seed(page, courses=COURSES, *, start=START, cap="3"):
    set_settings(page, start, cap)
    for course in courses:
        add_course(page, course)
    return generate(page, courses, start, cap)


def check_feasible(page):
    seed(page)


def check_shortage(page):
    courses = [
        {"name": "近期考试", "exam": "2099-06-03", "hours": "7"},
        {"name": "后续考试", "exam": "2099-06-05", "hours": "2"},
        {"name": "已到考试日", "exam": START, "hours": "1.5"},
    ]
    seed(page, courses, cap="2")


def check_edit_delete(page):
    seed(page)
    old = COURSES[0]
    course_row(page, old["name"]).get_by_role("button", name="编辑", exact=True).click()
    assert page.get_by_label("课程名称", exact=True).input_value() == old["name"]
    assert page.get_by_label("考试日期", exact=True).input_value() == old["exam"]
    assert decimal_hours(page.get_by_label("剩余小时", exact=True).input_value()) == Decimal(
        old["hours"]
    )
    changed = {"name": "线性代数", "exam": "2099-06-03", "hours": "1.5"}
    page.get_by_label("课程名称", exact=True).fill(changed["name"])
    page.get_by_label("考试日期", exact=True).fill(changed["exam"])
    page.get_by_label("剩余小时", exact=True).fill(changed["hours"])
    page.get_by_role("button", name="保存修改", exact=True).click()
    assert course_row(page, old["name"]).count() == 0, "Old course duplicated after edit"
    generate(page, [changed, *COURSES[1:]], START, "3")
    course_row(page, COURSES[1]["name"]).get_by_role("button", name="删除", exact=True).click()
    assert course_row(page, COURSES[1]["name"]).count() == 0, "Deleted course remains visible"
    generate(page, [changed, COURSES[2]], START, "3")


def check_settings(page):
    seed(page)
    set_settings(page, "2099-06-03", "1.5")
    generate(page, COURSES, "2099-06-03", "1.5")
    set_settings(page, START, "3")
    generate(page, COURSES, START, "3")


def check_refresh(page):
    before = seed(page)
    course_text = page.get_by_role("table", name="课程列表", exact=True).inner_text()
    page.reload(wait_until="networkidle")
    assert page.get_by_label("开始日期", exact=True).input_value() == START
    assert decimal_hours(page.get_by_label("每日上限（小时）", exact=True).input_value()) == 3
    assert page.get_by_role("table", name="课程列表", exact=True).inner_text() == course_text
    after = plan_rows(page)
    verify_schedule(after, COURSES, START, "3")

    def key(row):
        return row["日期"], row["课程"], decimal_hours(row["小时"])

    assert sorted(map(key, before)) == sorted(map(key, after)), "Plan lost/changed on refresh"


def check_invalid(page):
    set_settings(page, START, "3")
    for name, exam, hours in [
        ("", "2099-06-04", "1"),
        ("零小时", "2099-06-04", "0"),
        ("负小时", "2099-06-04", "-1"),
        ("无日期", "", "1"),
    ]:
        page.get_by_label("课程名称", exact=True).fill(name)
        page.get_by_label("考试日期", exact=True).fill(exam)
        page.get_by_label("剩余小时", exact=True).fill(hours)
        page.get_by_role("button", name="添加课程", exact=True).click()
        assert (
            page.get_by_role("table", name="课程列表", exact=True)
            .get_by_role("button", name="编辑", exact=True)
            .count()
            == 0
        ), "Invalid course was added"
    seed(page, [COURSES[0]])


def check_mobile(page):
    seed(page, [COURSES[0]])
    width = page.evaluate("({viewport: innerWidth, body: document.documentElement.scrollWidth})")
    assert width["body"] <= width["viewport"] + 1, "Page overflows the mobile viewport"
    course_row(page, COURSES[0]["name"]).get_by_role("button", name="删除", exact=True).click()
    generate(page, [], START, "3")


def evaluate(browser, origin, artifacts):
    checks, external = [], []
    scenarios = [
        ("feasible_multicourse", check_feasible),
        ("deadline_capacity_shortage", check_shortage),
        ("edit_delete_reschedule", check_edit_delete),
        ("settings_reschedule_clear_warning", check_settings),
        ("refresh_persistence", check_refresh),
        ("invalid_courses", check_invalid),
        ("mobile_interaction", check_mobile),
    ]
    for name, operation in scenarios:
        context = browser.new_context(
            viewport={"width": 390 if name == "mobile_interaction" else 1365, "height": 844},
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
            service_workers="block",
        )
        errors = []

        def route(request, case=name):
            if request.request.url.startswith(origin + "/"):
                request.continue_()
            else:
                external.append({"case": case, "url": request.request.url[:300]})
                request.abort()

        context.route("**/*", route)
        if hasattr(context, "route_web_socket"):

            def deny_socket(socket, case=name):
                external.append({"case": case, "url": socket.url[:300]})
                socket.close()

            context.route_web_socket("**/*", deny_socket)
        page = context.new_page()
        page.set_default_timeout(3500)
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        result = {"name": name, "passed": False}
        try:
            page.goto(origin + "/", wait_until="networkidle", timeout=15000)
            operation(page)
            assert not errors, "Uncaught browser errors: " + "; ".join(errors)
            result["passed"] = True
        except Exception as exc:
            result["detail"] = f"{type(exc).__name__}: {exc}"[:2500]
        finally:
            screenshot = artifacts / (name + ".png")
            try:
                page.screenshot(path=str(screenshot), full_page=True, timeout=5000)
                result["screenshot"] = str(screenshot)
            except Exception as exc:
                result["screenshot_error"] = type(exc).__name__
            result["browser_errors"] = errors
            checks.append(result)
            context.close()
    checks.append({"name": "no_external_network", "passed": not external, "requests": external})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(), args.output.resolve()
    artifacts = output.with_name(output.stem + "-screenshots")
    if output.exists() or artifacts.exists() or output.is_relative_to(candidate):
        parser.error("Use a new output path outside the candidate; previous results are immutable")
    if not (candidate / "index.html").is_file():
        parser.error("Candidate must contain index.html")
    artifacts.mkdir(parents=True)
    checks = [{"name": "readme_delivered", "passed": (candidate / "README.md").is_file()}]
    try:
        from playwright.sync_api import sync_playwright

        spec = importlib.util.spec_from_file_location("study_static_server", SERVER_PATH)
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        with server.static_server(candidate) as origin, sync_playwright() as playwright:
            with playwright.chromium.launch(headless=True) as browser:
                checks.extend(evaluate(browser, origin, artifacts))
    except Exception as exc:
        checks.append(
            {
                "name": "infrastructure",
                "passed": False,
                "detail": f"{type(exc).__name__}: {exc}"[:2000],
            }
        )
    result = {
        "candidate": str(candidate),
        "grader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "server_sha256": hashlib.sha256(SERVER_PATH.read_bytes()).hexdigest(),
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "manual_gates": [
            "ask_user_before_implementation",
            "aster_self_tests",
            "readme_preview_and_pages_instructions",
        ],
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": result["passed"], "output": str(output), "checks": len(checks)},
            ensure_ascii=False,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
