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

This directory retains the fixed task definitions and source hashes. The historical machine environment, preflight results and private preparation driver are not distributed. Prepare upstream dependencies independently; use the source metadata rather than the old local paths. These tasks participated in development after their original freeze and must not now be called unseen.

Licenses and source revisions reuse the same upstream releases as v2. Aider
exercise content is Exercism MIT (with the existing historical import-revision
limitation), BigCodeBench is Apache-2.0, Django is BSD-3-Clause, Sphinx BSD-2-Clause,
and SymPy BSD-3-Clause. Dataset/harness licensing does not replace project
licenses. Upstream notices remain in downloaded archives; license object hashes
are retained in `sources.json`. These tasks are evaluation-only, not training.

历史实验的设计取舍与证据边界见[优化历程](../../docs/optimization-history.md)；逐轮原始结果不随公开仓库分发。
