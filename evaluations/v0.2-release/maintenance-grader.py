"""Read-only browser acceptance: original Campus Planner checks plus undo deletion."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "benefit-20260918/check_browser.py"
SPEC = importlib.util.spec_from_file_location("maintenance_browser_contract", SOURCE)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
# BrowserAudit resolves artifact paths against its own module root. Rebase only
# those paths; the old evaluator file and all archived results remain untouched.
BASE.HERE = HERE
UNDO = "撤销删除"


def assert_tasks(actual, expected):
    assert isinstance(actual, dict) and actual.get("version") == 1, "invalid public storage package"
    tasks = actual.get("tasks")
    assert isinstance(tasks, list), "missing stored tasks"
    assert all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in tasks)
    ids = [item["id"] for item in tasks]
    assert len(ids) == len(set(ids)), "undo duplicated a stable task ID"
    assert {item["id"]: item for item in tasks} == {item["id"]: item for item in expected}, (
        "task fields, completion state, stable IDs or unrelated tasks changed"
    )


def delete(page, title):
    BASE.card(page, title).get_by_role("button", name="删除", exact=True).click()
    dialog = page.get_by_role("dialog")
    if dialog.count() and dialog.is_visible():
        dialog.get_by_role("button", name=re.compile("确认|确定|删除")).last.click()
    assert BASE.card(page, title).count() == 0, "confirmed deletion did not remove the task"


def undo(page):
    button = page.get_by_role("button", name=UNDO, exact=True)
    assert button.count() == 1 and button.is_visible() and button.is_enabled(), (
        "missing usable undo deletion button"
    )
    button.click()


def no_undo_effect(page, expected):
    button = page.get_by_role("button", name=UNDO, exact=True)
    assert button.count() <= 1, "ambiguous undo controls"
    if button.count() and button.is_visible() and button.is_enabled():
        button.click()
    assert_tasks(BASE.storage(page), expected)


def check_undo(audit):
    original = BASE.task(
        "undo-one",
        "保留完整内容的作业",
        course="数据结构",
        due="2099-08-09T08:15",
        notes="第一行\n第二行，保留标点；与空格 <文本>",
    )
    sibling = BASE.task("unchanged", "不应改变的另一条", course="英语", notes="保持原样")

    def fields(page):
        delete(page, original["title"])
        assert_tasks(BASE.storage(page), [sibling])
        undo(page)
        assert_tasks(BASE.storage(page), [original, sibling])
        assert BASE.card(page, original["title"]).count() == 1
        assert original["notes"] in BASE.card(page, original["title"]).inner_text()
        page.reload(wait_until="networkidle")
        assert_tasks(BASE.storage(page), [original, sibling])
        assert BASE.card(page, original["title"]).count() == 1

    audit.run("undo-restores-fields-id-and-persists", fields, tasks=[original, sibling])

    finished = {**original, "completed": True}

    def completed(page):
        delete(page, finished["title"])
        undo(page)
        assert_tasks(BASE.storage(page), [finished, sibling])
        assert (
            BASE.card(page, finished["title"])
            .get_by_role("button", name="恢复", exact=True)
            .is_visible()
        )

    audit.run("undo-preserves-completed-state", completed, tasks=[finished, sibling])
    second = BASE.task("undo-two", "第二次删除的作业", notes="只能撤销这一条")

    def latest(page):
        delete(page, original["title"])
        delete(page, second["title"])
        undo(page)
        assert_tasks(BASE.storage(page), [second, sibling])
        assert BASE.card(page, original["title"]).count() == 0
        assert BASE.card(page, second["title"]).count() == 1
        no_undo_effect(page, [second, sibling])

    audit.run("undo-restores-only-latest-deletion", latest, tasks=[original, second, sibling])

    def repeated(page):
        delete(page, original["title"])
        undo(page)
        no_undo_effect(page, [original, sibling])
        no_undo_effect(page, [original, sibling])
        assert BASE.card(page, original["title"]).count() == 1

    audit.run("undo-repeat-cannot-duplicate-task", repeated, tasks=[original, sibling])

    def refresh(page):
        delete(page, original["title"])
        button = page.get_by_role("button", name=UNDO, exact=True)
        assert button.count() == 1 and button.is_visible() and button.is_enabled()
        page.reload(wait_until="networkidle")
        assert_tasks(BASE.storage(page), [sibling])
        no_undo_effect(page, [sibling])
        assert BASE.card(page, original["title"]).count() == 0

    audit.run("undo-refresh-clears-deletion-record", refresh, tasks=[original, sibling])

    def mobile(page):
        delete(page, original["title"])
        undo(page)
        assert_tasks(BASE.storage(page), [original])
        assert BASE.card(page, original["title"]).count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
            "mobile page overflows horizontally"
        )

    audit.run(
        "undo-mobile-delete-and-restore", mobile, tasks=[original], mobile=True, screenshot=True
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace, output = args.workspace.resolve(), args.output.resolve()
    if not (workspace / "index.html").is_file():
        parser.error("workspace must contain index.html")
    if output.exists() or output.is_relative_to(workspace) or not output.is_relative_to(HERE):
        parser.error(
            "output must be a new path under this evaluation directory, outside the candidate"
        )
    result = {
        "workspace": str(workspace),
        "cases": [],
        "errors": [],
        "screenshots": [],
        "grader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "base_grader_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    }
    try:
        from playwright.sync_api import sync_playwright

        with BASE.static_server(workspace) as origin, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                audit = BASE.BrowserAudit(browser, origin, output)
                for check in (BASE.check_base, BASE.check_backup, BASE.check_calendar, check_undo):
                    check(audit)
                audit.cases.append(
                    {
                        "name": "no-external-network",
                        "passed": not audit.external_requests,
                        "requests": audit.external_requests,
                    }
                )
                result.update(
                    cases=audit.cases, screenshots=audit.screenshots, downloads=audit.downloads
                )
                result["errors"] = [
                    {"case": row["name"], "error": row.get("error", "external request")}
                    for row in audit.cases
                    if not row["passed"]
                ]
            finally:
                browser.close()
    except Exception as exc:
        result["errors"].append({"infrastructure": f"{type(exc).__name__}: {exc}"[:2000]})
    result["totals"] = {
        "passed": sum(row["passed"] for row in result["cases"]),
        "executed": len(result["cases"]),
    }
    result["passed"] = (
        len(result["cases"]) == 32
        and not result["errors"]
        and all(row["passed"] for row in result["cases"])
    )
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
