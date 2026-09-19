"""Read-only browser acceptance for the refreshed static tutorial; no model requests."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright

SERVER_PATH = Path(__file__).resolve().parent.parent / "benefit-20260918/check_browser.py"
SPEC = importlib.util.spec_from_file_location("tutorial_static_server", SERVER_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


def fingerprint(site):
    return {
        str(path.relative_to(site)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(site.rglob("*"))
        if path.is_file()
    }


def evaluate(browser, origin, screenshots, terms):
    checks, page_errors, request_errors = [], [], []
    home = origin + "/index.html"

    def record(name, operation, page):
        try:
            detail = operation()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": str(exc)[:2000]})
            page.screenshot(path=str(screenshots / f"failed-{name}.png"))

    def no_overflow(page):
        dimensions = page.evaluate(
            "({viewport: innerWidth, document: document.documentElement.scrollWidth})"
        )
        assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions

    for device, viewport in [
        ("desktop", {"width": 1440, "height": 1000}),
        ("mobile", {"width": 390, "height": 844}),
    ]:
        context = browser.new_context(
            viewport=viewport,
            is_mobile=device == "mobile",
            has_touch=device == "mobile",
            reduced_motion="reduce",
            permissions=["clipboard-read", "clipboard-write"],
        )
        context.set_default_timeout(5000)
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                request_errors.append({"url": response.url, "status": response.status})
                if response.status >= 400
                else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: request_errors.append({"url": request.url, "failure": request.failure}),
        )

        def chapters(page=page, device=device, viewport=viewport):
            page.goto(home)
            expect(page.locator("h1")).to_be_visible()
            links = page.locator(".lesson-grid a")
            expect(links).to_have_count(16)
            destinations = links.evaluate_all("nodes => nodes.map(node => node.href)")
            no_overflow(page)
            page.screenshot(path=str(screenshots / f"{device}-home.png"))
            for destination in destinations:
                response = page.goto(destination)
                assert response and response.status == 200
                expect(page.locator("article h1")).to_be_visible()
                assert len(page.locator("article").inner_text()) > 500, "Empty/truncated chapter"
                no_overflow(page)
            return {"chapter_pages": len(destinations), "viewport": viewport}

        record(f"{device}-all-chapters-layout", chapters, page)

        def navigation(page=page, device=device):
            page.goto(home)
            page.locator(".lesson-grid a").first.click()
            expect(page).to_have_url(origin + "/lessons/00-quickstart.html")
            next_link = page.locator(".chapter-nav a[href='01-agent-loop.html']")
            next_link.click()
            expect(page).to_have_url(origin + "/lessons/01-agent-loop.html")
            page.locator(".chapter-nav a[href='00-quickstart.html']").click()
            expect(page).to_have_url(origin + "/lessons/00-quickstart.html")
            if device == "mobile":
                page.locator("#menu-toggle").click()
                expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
            page.locator(".sidebar a[href='13-interaction-and-workers.html']").click()
            expect(page).to_have_url(origin + "/lessons/13-interaction-and-workers.html")
            if device == "mobile":
                expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
                page.locator("#menu-toggle").click()
                page.keyboard.press("Escape")
                expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
            page.goto(home)
            page.locator("#continue-link").click()
            expect(page).to_have_url(origin + "/lessons/13-interaction-and-workers.html")

        record(f"{device}-chapter-navigation", navigation, page)

        def preferences(page=page, device=device):
            page.goto(origin + "/lessons/03-tool-system.html")
            page.locator("#theme-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            page.reload()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            checkbox = page.locator(".completed-toggle")
            checkbox.check()
            expect(page.locator(".progress-text")).to_have_text("1 / 16 章已完成")
            page.reload()
            expect(checkbox).to_be_checked()
            expect(page.locator(".progress-text")).to_have_text("1 / 16 章已完成")
            page.screenshot(path=str(screenshots / f"{device}-chapter-dark.png"))
            checkbox.uncheck()
            expect(page.locator(".progress-text")).to_have_text("0 / 16 章已完成")
            page.locator("#theme-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "light")
            page.screenshot(path=str(screenshots / f"{device}-chapter-light.png"))

        record(f"{device}-theme-and-progress-persistence", preferences, page)

        def search(page=page, device=device):
            matches = []
            for term in terms:
                page.goto(home)
                page.locator("#search-open").click()
                page.get_by_role("searchbox").fill(term)
                first = page.locator(".search-results a").first
                expect(first).to_be_visible()
                destination = urljoin(home, first.get_attribute("href"))
                matches.append({"term": term, "url": destination.removeprefix(origin)})
                page.screenshot(path=str(screenshots / f"{device}-search-{len(matches)}.png"))
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                expect(page).to_have_url(destination)
                assert term.casefold() in page.locator("article").inner_text().casefold()
            page.locator("#search-open").click()
            page.keyboard.press("Escape")
            expect(page.locator(".search-dialog")).to_have_count(0)
            expect(page.locator("#search-open")).to_be_focused()
            return matches

        record(f"{device}-updated-content-search", search, page)

        def copy(page=page):
            page.goto(origin + "/lessons/03-tool-system.html")
            frame = page.locator(".code-frame").first
            expected = frame.locator("pre").text_content()
            assert expected and expected.strip(), "No code content to copy"
            button = frame.locator(".copy-code")
            button.click()
            expect(button).to_have_text("已复制")
            assert page.evaluate("navigator.clipboard.readText()") == expected
            return {"copied_characters": len(expected)}

        record(f"{device}-clipboard", copy, page)
        context.close()

    checks.append({"name": "no-browser-errors", "passed": not page_errors, "detail": page_errors})
    checks.append(
        {"name": "no-request-errors", "passed": not request_errors, "detail": request_errors}
    )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Final learn_web directory to serve read-only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--search", action="append", required=True, help="Updated text to find; repeatable"
    )
    args = parser.parse_args()
    site, output = args.site.resolve(), args.output.resolve()
    screenshots = output.with_name(output.stem + "-screenshots")
    if output.exists() or screenshots.exists() or output.is_relative_to(site):
        parser.error("Use a new output path outside the site; previous evidence is immutable")
    if not (site / "index.html").is_file():
        parser.error("Site must contain index.html")
    screenshots.mkdir(parents=True)
    before = fingerprint(site)
    checks, version = [], ""
    try:
        with SERVER.static_server(site) as origin, sync_playwright() as playwright:
            with playwright.chromium.launch(headless=True) as browser:
                version = browser.version
                checks = evaluate(browser, origin, screenshots, args.search)
    except Exception as exc:
        checks.append({"name": "infrastructure", "passed": False, "detail": str(exc)[:2000]})
    checks.append({"name": "site-files-unchanged", "passed": before == fingerprint(site)})
    result = {
        "site": str(site),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "server_sha256": hashlib.sha256(SERVER_PATH.read_bytes()).hexdigest(),
        "site_manifest_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "browser": "Chromium " + version,
        "search_terms": args.search,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "screenshots": [str(path) for path in sorted(screenshots.glob("*.png"))],
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
