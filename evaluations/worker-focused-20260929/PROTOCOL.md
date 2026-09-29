# Focused Worker development replication, 2026-09-29

This is a **development re-evaluation of four previously seen tasks**, not a
holdout or a representative benchmark. The fixed selection is recorded by
reference and hash in `datasets/worker-focused-20260929/selection.json`:
Django-16256, SymPy-14248, OpenLibrary authors, and SymPy-13878. Two are medium
and two hard under the existing Aster strata. They were chosen for multiple
required implementation areas after their earlier outcomes were available.
No task is added, replaced or dropped in response to this run's results.

The experiment has 4 tasks × 3 repeats × 2 arms = **24 episodes**. Both arms
load exactly the same frozen source commit and checkout. Only `max_workers`
differs: `solo=0`, `worker=2`. The worker arm decides whether and how to delegate;
there is no forced decomposition or delegation hint beyond the unchanged
neutral common instruction. Both arms use the same internal
`TaskSpec.max_tokens` and the same external whole-team limits:

| stratum | model requests | tools | reported token ceiling | seconds |
| --- | ---: | ---: | ---: | ---: |
| medium | 60 | 90 | 750,000 | 900 |
| hard | 80 | 120 | 1,000,000 | 1,200 |

Internal estimated reservations may stop requests below the reported ceiling;
this now applies to both arms. Model, endpoint, context/output limits, memory
disabled settings, original prompt, neutral common suffix and public repository
test guidance are identical within each task. Source and settings are frozen
in a private `run-lock.json` before the first request. No cheaper worker model
or larger team budget is used. There is no new prompt content from selection
reasons, omission categories, hidden tests or solutions.

All episodes are fresh processes and fresh workspaces, executed serially in
task order then repeat order. Arm order is solo/worker when
`(task_index + repeat - 1) % 2 == 0`, otherwise worker/solo. Across all tasks,
six pairs start with each arm. Repeats are three independent model samples,
not retries of failures. A stopped/failed/timeout episode remains a result.
Resume skips existing result files and refuses incomplete directories; no
best-of-three selection or performance-driven retry is allowed.

The driver imports the existing preparation, scoring, environment and request
ledger from `worker-code-20260928` through `worker-redesign-20260928`. It keeps
the same original tests, immutable test restoration and existing prepared
dependencies. Four successful starter/reference gates are reused with their
exact hashes and unchanged grader/launcher hashes. Their required-test counts
are 62, 435, 8 and 20. This is native-equivalent adapted scoring, not an official
SWE-bench leaderboard score. Public native test support being repaired in
Aster belongs in the common source commit, not a changed independent oracle.

Primary results are independent task pass rate over **all 12 episodes per
arm**, paired wins/losses, and **correct normal delivery**: independent pass
AND runtime phase `completed` AND stop reason `verified` AND no episode error.
Keep these distinct: a correct patch after cancellation is not normal delivery;
an agent's verified state does not guarantee independent correctness. Report
each task's 3/3, 2/3, 1/3 or 0/3 outcomes and all failures.

Whole-team input/output/cache/reasoning/reported total tokens, wall seconds,
model/tool calls and coordination calls include every parent and child. Report
all episodes first. Then report costs on paired repeats where **both arms
passed**, and separately where **both achieved correct normal delivery**.
If the eligible set is empty, say so. These subsets are descriptive, not new
primary outcomes. No confirmed endpoint prices are available: do not invent
dollar costs. Preserve HTTP attempts without usage and show their count beside
reported token totals. Wall time excludes independent grading and may reflect
endpoint latency; it does not by itself establish parallelism benefit.

Omission categories are frozen in `selection.json` from original requirements:
ordinary versus generic related managers; str/pretty/LaTeX printers; author
resolution priority versus query semantics; and the twelve named distributions.
The report includes matching independent subtest statuses as **diagnostics**,
not substitutes for whole-task pass. A failed check alone is not proof of an
omitted implementation. An omission requires code/trajectory evidence of a
required area left unimplemented. Implemented-but-wrong, omitted, verified,
and unknown are separate manual labels. No evidence means `unknown`.

Two inherited oracle limitations must remain visible. The SymPy-14248 official
parser collapses same-named tests across printer files; its F2P short names
cannot prove coverage of each printer. The SymPy-13878 required set contains
only Arcsin as F2P plus 19 regression checks, despite twelve distributions in
the original request. Other distribution requirements remain unknown without
manual or supplementary diagnostic evidence. Do not relabel this task's
original whole-task score as complete coverage of all twelve distributions.

Worker contributions require manual trace evidence: concrete assigned scope,
child action/result, model-authored versus deterministic handoff, partial versus
complete delivery, whether a subsequent parent request consumed it, and a
specific resulting edit/check/decision. A persisted handoff or completed child
is not automatically a contribution. Report actual worker use as a post-hoc
descriptive subgroup; zero-worker differences cannot be attributed to
delegation. All original parent/child traces, messages, requests, ledgers,
workspaces and independent grading logs stay in the private output directory.

Each launch verifies loaded Aster module paths and source hashes against the
bound commit. Frozen hashes cover the driver, protocol, selection, referenced
datasets, old adapters, environment helper, ledger, official parser and sandbox
launcher. The summarizer checks paired identity and reconciles reported HTTP
usage, runtime totals and parent/child totals. Use Conda `aster` Python 3.12;
never base. `audit` performs no model request.

```bash
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-focused-20260929/evaluate.py audit
# Both keys must contain the exact same root and full commit.
# {"solo":{"root":"/frozen/checkout","commit":"..."},"worker":{"root":"/frozen/checkout","commit":"..."}}
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-focused-20260929/evaluate.py run --sources /path/to/source-bindings.json
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-focused-20260929/summarize.py
```

Private outputs: `artifacts/private/worker-focused-20260929/repeat-01` through
`repeat-03`. No paid run is authorized by `audit`, protocol creation or gate reuse.
