# Offline verification before source binding

## Product regression

The final source loaded from `/home/dzh/Aster/src/aster/__init__.py` under
`/home/dzh/.conda/envs/aster/bin/python`. Full offline regression passed:
**1,073 passed / 4 skipped**, in 101.56 seconds (`pytest -q`, including the
learning-site tests). Ruff and `git diff --check` passed. The runnable examples
from learning chapters 12 and 13 passed, and `learn_web/build.py` regenerated
the site from current source and tutorials.

A wheel built from the final source was installed into a fresh venv derived
from Conda `aster`. With `PYTHONPATH` unset and cwd `/tmp`, all 54 installed
Python files matched both the wheel and current source byte-for-byte; imports
resolved to the new venv's `site-packages` and were not editable. CLI help,
offline RL smoke, real PTY Chinese session selection/restoration and `quit`,
and default `aster` TUI startup/quit passed without a model call or traceback.
Full commands, interpreter provenance and hashes are in `wheel-verification.json`.
The copied PTY script initially expected an old picker title; its assertion was
updated to the actual current UI title, with no product change, then rerun.

The first full run found one real regression: the added common requirement
guidance pushed the minimum four-result Chat request two tokens past its 4K
context budget. The guidance was shortened; long acceptance/resolution prose
now participates in the existing workflow compaction limit. Existing tests were
extended to cover these fields, and all 22 context regression cases passed
before the final full run. No budget or assertion was relaxed.

Independent public-API review additionally exercised premature Final in
strict/warn/off modes, training termination, real child edits plus parent tests,
rejected launches, stale delivery after reassignment, cold recovery, token
reserve reset between runs, and cancellation scope across continued/new requests.
Cancellation warnings persist for the same task and do not contaminate a later
user request. These checks establish state contracts, not model correctness.

Production Python remains **54 files**, increasing from **11,351 to 11,476
physical lines (+125)**. No runtime dependency was added. The implementation
reuses Runtime, todos, existing shared budgets and checkpoints; it does not add
an orchestration framework. See `code-metrics.json` for the baseline identity.

## Driver checks

On 2026-09-30, all commands below passed using Conda `aster` Python 3.12.
No model request or benchmark test was launched by this driver validation.

```bash
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/checks.py
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/evaluate.py audit
PYTHONPATH=/tmp/aster-worker-owned-current-20260930/src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/evaluate.py audit --arm current --source-root /tmp/aster-worker-owned-current-20260930 --source-commit 6ccb8be1ef994f490eaa79a3786c0fb9c18df7e4
/home/dzh/.conda/envs/aster/bin/python -B evaluations/worker-owned-20260930/diagnostics/batch.py audit
/home/dzh/.conda/envs/aster/bin/python -m py_compile evaluations/worker-owned-20260930/evaluate.py evaluations/worker-owned-20260930/summarize.py evaluations/worker-owned-20260930/checks.py
git diff --check
```

The runnable check uses temporary synthetic accounting only. It verifies the
external selection and schedule, source-binding rejection, empty/partial/full
aggregation, paired success subsets, unknown HTTP attempts, separate child usage,
and retention of initialization errors, grader errors and incomplete directories.
Corrupted source hashes, budgets, prompts, usage and child counts are rejected.
The fixture files never enter the experiment's private output directories.

The independent old-source audit resolves Aster to
`/tmp/aster-worker-owned-current-20260930/src/aster/__init__.py`. All ten reused
starter/reference gates pass their hash, readiness and required-count checks.
The new product source is frozen at
`a3d3364147fdb27eeaf9d1308317fbf594137611` in
`/tmp/aster-worker-owned-new-20260930`, shared by solo and worker.
The full `audit --sources artifacts/private/worker-owned-20260930/source-bindings.json`
passed for all three isolated bindings before paid launch. The source commit
is independent of the later dataset/driver/report commits.

Supplementary freeze is present and audited at SHA-256
`e4513442f9e21d616073710e6022b94112f73036c651b927edcdfa660c165192`.
The paid controller requires it before launch, saves a byte snapshot and checks
the same digest before/after each episode. The two controllers share one lock.

## Resource estimate, not a run result

The thirty historical `worker-redesign-20260928` episodes for exactly these ten
tasks averaged 122 seconds per simple episode, 356 seconds per medium episode
and 455 seconds per hard episode. Replaying twice that workload projects
18,332 agent seconds (5.1 hours), plus approximately 134 grading seconds and
preparation/controller overhead. Allow roughly **5–7 hours**, subject to model
latency and the new implementation. This estimate is not an outcome claim.

Serial agent time ceilings are 600 / 900 / 1,200 seconds per simple / medium /
hard episode. Across 60 episodes they sum to **14.5 hours**, excluding preparation,
independent grading and cleanup. The HTTP request timeout is 120 seconds;
maximum two children share each episode's team budgets with the parent.

The same thirty prior workspaces occupy 5.76 GiB, projecting approximately
11.5 GiB for sixty workspaces, plus logs and checkpoints. At inspection `/home`
has 133 GiB free, `/tmp` has 47 GiB free, and memory availability is about 81 GiB.
The serial controller does not run competing benchmark episodes. Supplemental
diagnostics begin only after all sixty results exist and have a separate wall
time budget; see `SUPPLEMENTARY-PROTOCOL.md`.
