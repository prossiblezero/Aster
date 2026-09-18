"""Operator-owned assertions, never placed in the model's workspace."""
import json
import subprocess
import sys


def invoke(tmp_path, text):
    source = tmp_path / "sample.csv"
    source.write_text(text, encoding="utf-8")
    result = subprocess.run([sys.executable, "csvtotals.py", str(source)], capture_output=True, text=True)
    assert source.read_text(encoding="utf-8") == text
    return result


def test_grouping_precision_and_rounding(tmp_path):
    result = invoke(tmp_path, '\ufeffcategory,amount,note\n z ,0.005,x\na,1.005,x\nz,0.005,y\na,-0.005,z\n"a,b",-1.235,q\n\n')
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {"a": "1.00", "a,b": "-1.24", "z": "0.01"}
    assert list(json.loads(result.stdout)) == ["a", "a,b", "z"]


def test_empty_input_and_exact_decimal(tmp_path):
    assert json.loads(invoke(tmp_path, "category,amount\n").stdout) == {}
    result = invoke(tmp_path, "category,amount\nx,9007199254740993.01\nx,0.02\n")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"x": "9007199254740993.03"}


def test_errors_are_clean(tmp_path):
    for text in ["category,price\nx,1\n", "category,amount\nx,NaN\n", "category,amount\nx,Infinity\n", "category,amount\nx,nope\n", "category,amount\n ,1\n"]:
        result = invoke(tmp_path, text)
        assert result.returncode == 2 and not result.stdout and result.stderr.strip()
    result = subprocess.run([sys.executable, "csvtotals.py", "missing-file.csv"], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout and result.stderr.strip()
