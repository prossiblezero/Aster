"""Read structured test outcomes; callers decide whether optional skips are acceptable."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def parse_junit(text: str) -> list[dict[str, str]]:
    root = ET.fromstring(text)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("invalid_junit_report")
    cases = []
    for case in root.iter("testcase"):
        if not case.get("name"):
            raise ValueError("unnamed_junit_test")
        children = {child.tag for child in case}
        status = next(
            (
                result
                for tag, result in (
                    ("error", "error"),
                    ("failure", "failed"),
                    ("skipped", "skipped"),
                )
                if tag in children
            ),
            "passed",
        )
        cases.append(
            {"name": case.get("name"), "classname": case.get("classname", ""), "status": status}
        )
    return cases
