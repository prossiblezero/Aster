"""Deterministic paired mechanism evaluation, separate from real-model task scores."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from benchmark import source_identity
from mechanism_support import run_case

DATASETS = Path(__file__).resolve().parents[1] / "datasets"


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[f"{row['suite']}/{row['variant']}"].append(row)
    return {
        name: {
            "attempts": len(group),
            "contract_passed": sum(r["contract_passed"] for r in group),
            "goal_passed": sum(r["goal_passed"] for r in group),
        }
        for name, group in sorted(groups.items())
    }


async def evaluate(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    memory = json.loads((DATASETS / "memory_evolution_cases.json").read_text())
    scenarios = [
        (case, variant) for case in memory["cases"] for variant in ("evolution_off", "evolution_on")
    ]
    runtime_path = DATASETS / "runtime_cases.json"
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text())
        scenarios.extend(
            (dict(runtime["defaults"], **case), variant)
            for case in runtime["cases"]
            for variant in case["variants"]
        )
    rows = []
    scenarios.extend(
        (dict(case, kind="tool_contract"), "current")
        for case in json.loads((DATASETS / "tool_contract_cases.json").read_text())
    )
    for case, variant in scenarios:
        destination = output / "runs" / stamp / case["id"] / variant
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aster-mechanism-") as temporary:
            root = Path(temporary) / "workspace"
            try:
                row = await run_case(case, root, variant=variant)
            except Exception as exc:
                row = {
                    "case_id": case["id"],
                    "suite": case.get("suite", case["kind"]),
                    "variant": variant,
                    "contract_passed": False,
                    "goal_passed": False,
                    "checks": {"scenario_execution": False},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if root.joinpath(".aster").exists():
                shutil.copytree(root / ".aster", destination / "state", dirs_exist_ok=True)
        row.pop("memory_state", None)
        row["artifact"] = str(destination.relative_to(output))
        (destination / "result.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n"
        )
        rows.append(row)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source": source_identity(),
        "mode": "deterministic_mechanism_scenarios",
        "limitations": [
            "Synthetic fixed action sequences and supplied feedback labels; not real-model task success.",
            "Contract pass matches each arm's declared behavior; goal pass uses the same desired outcome across arms.",
            "Memory admission/freshness safety remains enabled in both evolution arms.",
        ],
        "unique_cases": len({r["case_id"] for r in rows}),
        "attempts": len(rows),
        "contract_passed": sum(r["contract_passed"] for r in rows),
        "summary": summarize(rows),
        "rows": rows,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Aster 机制评测",
        "",
        "固定场景与脚本动作，用于验证机制及消融；不是模型能力总分。",
        "反馈场景使用预先标注的成功/失败，不代表真实模型中的记忆自进化收益。",
        "",
        "契约通过：符合该组预期行为。目标达成：开启/关闭两组使用同一目标，允许关闭组未达成。",
        "",
        "| 场景 / 组别 | 尝试 | 契约通过 | 目标达成 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, group in report["summary"].items():
        lines.append(
            f"| {name} | {group['attempts']} | {group['contract_passed']} | {group['goal_passed']} |"
        )
    lines.extend(
        [
            "",
            f"总计 {report['unique_cases']} 个场景、{report['attempts']} 次尝试；"
            f"契约通过 {report['contract_passed']}/{report['attempts']}。",
            "每次执行的完整状态和判定保存在 runs/ 下；失败不从统计中删除。",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark-v4-mechanisms"))
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.output))
    print(
        json.dumps(
            {k: report[k] for k in ("unique_cases", "attempts", "contract_passed", "summary")},
            indent=2,
        )
    )
    raise SystemExit(0 if report["contract_passed"] == report["attempts"] else 1)


if __name__ == "__main__":
    main()
