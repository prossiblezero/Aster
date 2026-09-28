# Worker redesign: frozen six-task holdout

These six original upstream tasks were selected on 2026-09-28 before running
their preflight scorers or any Aster model episode. They do not overlap the
v1/v2 selection. They are new to this Aster evaluation, not guaranteed unseen
to the model during training. The previous 20 v2 tasks remain a separate
regression group; never combine the two groups into a claim of unseen accuracy.

| Source | Original task | Stratum |
|---|---|---|
| Aider Polyglot Python | affine-cipher | simple |
| BigCodeBench v0.1.4 | BigCodeBench/30 | simple |
| SWE-bench Verified | django__django-11138 | medium |
| SWE-bench Verified | django__django-13212 | medium |
| SWE-bench Verified | sphinx-doc__sphinx-8548 | medium |
| SWE-bench Verified | sympy__sympy-13878 | hard |

The four repository tasks retain their official expert difficulty: three
`1-4 hours` and one `>4 hours`. The two small standard-library tasks are overhead
controls. No problem was assembled by concatenating exercises. Task prompts,
source revisions, reference hashes, independent grading contracts and selection
reasons live in `instances.jsonl`, `sources.json`, and `manifest.json`; the
driver reads them by reference. `collaboration` annotations are control-plane
audit data, never additional hints given to the model.

All six initial implementations fail and all six upstream reference solutions
pass the same restricted independent scorer used for all three arms. The
20 old gates are reused with exact file hashes because tasks, independent
scorers and prepared dependencies are unchanged. This does not produce official
SWE-bench/Aider/BigCodeBench leaderboard scores.

Preflight found two environment mismatches; both original failed attempts are
preserved privately. BigCodeBench/30 writes hard-coded `/tmp` fixture files;
only those paths are relocated into the grading workspace. Django-13212 predates
Python3.9.25's stricter IPv6 URL parsing, so its separate aster-derived runtime
uses the exact official CPython3.9.0 `urllib.parse` recorded in `environment.json`.
The rest of the existing Python3.9 dependency environment is unchanged. Its
unittest subTest log formatting is normalized to recover complete test IDs;
no assertion or required test is removed. The initial task selection was kept.

```bash
conda activate aster
python evaluations/worker-redesign-20260928/prepare.py
python evaluations/worker-redesign-20260928/evaluate.py audit
python evaluations/worker-redesign-20260928/evaluate.py preflight --split holdout
```

`prepare.py --fetch` downloads only missing, hash-pinned upstream assets. If
derived SWE records must be rebuilt, supply an existing PyArrow installation
with `--pyarrow-root` or install it in an aster-derived preparation environment;
the Aster runtime gains no dependency. Original source objects and oracle rows
stay under `artifacts/private/worker-redesign-20260928/objects`, outside model
workspaces. Aider public tests remain public; each scorer uses a protected copy.

Licenses and source revisions reuse the same upstream releases as v2. Aider
exercise content is Exercism MIT (with the existing historical import-revision
limitation), BigCodeBench is Apache-2.0, Django is BSD-3-Clause, Sphinx BSD-2-Clause,
and SymPy BSD-3-Clause. Dataset/harness licensing does not replace project
licenses. Upstream notices remain in downloaded archives; license object hashes
are retained in `sources.json`. These tasks are evaluation-only, not training.

The [three-arm protocol](../../evaluations/worker-redesign-20260928/PROTOCOL.md)
defines fixed team budgets, source binding and sequential counterbalanced order.
No paid model result existed when this holdout and its environment were frozen.
