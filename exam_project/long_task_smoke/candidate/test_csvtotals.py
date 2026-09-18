import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("csvtotals.py")


def run_tool(tmp_path: Path, content: str | bytes, name: str = "input.csv"):
    input_path = tmp_path / name
    if isinstance(content, bytes):
        input_path.write_bytes(content)
    else:
        input_path.write_text(content, encoding="utf-8", newline="")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(input_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_sums_sorts_trims_and_handles_quoted_categories(tmp_path):
    result = run_tool(
        tmp_path,
        "category,amount,note\n"
        '  Zebra  ,1.005,first\n'
        '"Food, drinks",2.004,x\n'
        "Zebra,2.004,y\n"
        '"Food, drinks",-0.999,z\n'
        ",,\n"
        "   ,   ,   \n",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"Food, drinks": "1.01", "Zebra": "3.01"}
    assert result.stdout.index("Food, drinks") < result.stdout.index("Zebra")
    assert result.stderr == ""


def test_rounds_only_after_group_sum_using_half_up(tmp_path):
    result = run_tool(tmp_path, "category,amount\na,0.004\na,0.001\nb,-1.005\n")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"a": "0.01", "b": "-1.01"}


def test_accepts_utf8_bom_and_unicode(tmp_path):
    content = "category,amount\n咖啡,12.5\n".encode("utf-8-sig")
    result = run_tool(tmp_path, content)

    assert result.returncode == 0
    assert result.stdout == '{"咖啡": "12.50"}\n'


def test_header_only_outputs_empty_object(tmp_path):
    result = run_tool(tmp_path, "category,amount,other\n")

    assert result.returncode == 0
    assert result.stdout == "{}\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "content",
    [
        "category,other\na,x\n",
        "amount,other\n1,x\n",
        "category,amount\na,not-a-number\n",
        "category,amount\na,NaN\n",
        "category,amount\na,Infinity\n",
        "category,amount\n   ,1\n",
        'category,amount\n"unterminated,1\n',
    ],
)
def test_bad_input_exits_two_without_stdout(tmp_path, content):
    result = run_tool(tmp_path, content)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error:")


def test_missing_file_exits_two_without_stdout(tmp_path):
    missing = tmp_path / "missing.csv"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(missing)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error:")


def test_large_values_are_added_without_decimal_context_rounding(tmp_path):
    result = run_tool(
        tmp_path,
        "category,amount\nlarge,9999999999999999999999999999.99\nlarge,0.01\n",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"large": "10000000000000000000000000000.00"}
