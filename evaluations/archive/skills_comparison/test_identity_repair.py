"""Regression for the startup-only task identity comparison repair."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("skills_identity_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def launch():
    return {
        "task": {"task_id": "random-one", "prompt": "same task", "max_steps": 30},
        "configuration": {"model": "same-model"},
        "source": {"sha256": "same-source"},
        "connection_sha256": "same-connection",
        "experiment_files": {
            "run.py": hashlib.sha256((HERE / "driver-v1.py").read_bytes()).hexdigest(),
            "checks.py": "same-checks",
        },
    }


def test_random_id_is_not_a_contract_change_and_driver_repair_is_recorded():
    old = launch()
    new = deepcopy(old)
    new["task"]["task_id"] = "random-two"
    assert driver.comparable_launch(old, new)
    new["experiment_files"]["run.py"] = "revised-driver"
    assert driver.comparable_launch(old, new)
    assert new["driver_amendment"]["original_sha256"] == old["experiment_files"]["run.py"]


@pytest.mark.parametrize("field", ["prompt", "max_steps", "checks.py", "model"])
def test_real_contract_and_experiment_changes_remain_rejected(field):
    old = launch()
    new = deepcopy(old)
    if field in {"prompt", "max_steps"}:
        new["task"][field] = "changed"
    elif field == "checks.py":
        new["experiment_files"][field] = "changed"
    else:
        new["configuration"][field] = "changed"
    assert not driver.comparable_launch(old, new)
