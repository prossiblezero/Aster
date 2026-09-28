# Worker redesign: three-arm comparison, 2026-09-28

This protocol is frozen before paid model calls. Reuse the exact 20 tasks in
`datasets/worker-code-v2` as the **regression** group and six newly selected,
disjoint tasks in `datasets/worker-code-v3-holdout` as the **holdout** group.
The holdout has two simple, three medium and one hard task from Aider,
BigCodeBench and SWE-bench Verified. Prompts, reference solutions, tests, IDs
and selection reasons live in the datasets, never inside the evaluation driver.
No task is replaced after observing model outcomes. Preparation failures remain
visible in coverage rather than becoming silently replaced tasks.

| Arm | Workers | Product behavior |
|---|---:|---|
| solo | 0 | Shared foundation tool fixes, delegation disabled |
| handoff_only | 2 | Original bounded Worker plus final no-tool handoff and persistent partial results |
| redesigned | 2 | New Worker lifecycle, handoff and shared team budgets |

Exact source commits and independent source roots are supplied by a frozen
private JSON binding for all three arms. Each episode runs in its own process,
imports Aster from that root explicitly, checks every loaded Aster module,
requires the product tree to match the bound commit, and captures source hashes.
All arms must include the same foundation tool fixes; the final report records
the actual commit differences. This is not a comparison against the old run's
uncorrected tools. Aster uses Conda `aster` Python 3.12 or an aster-derived
environment, never base. Repository tests reuse the existing derived runtimes.
The shared baseline is commit `8377327` (`worker-handoff-baseline-20260928`),
available at `/tmp/aster-worker-handoff-20260928`; the redesigned commit must
be supplied after its implementation and checks are complete.

All arms use the same configured `.env` model and endpoint, Responses streaming,
64k context, 8192 output, strict completion, offline restricted execution,
memory disabled, original problem statement and neutral common instructions.
No decomposition, answer locations or forced worker count are supplied.
The unchanged external Ledger counts every parent/child request and tool.

| Difficulty | Team model calls | Team tools | Reported total tokens | Wall seconds |
|---|---:|---:|---:|---:|
| simple | 40 | 60 | 500000 | 600 |
| medium | 60 | 90 | 750000 | 900 |
| hard | 80 | 120 | 1000000 | 1200 |

Solo/handoff_only retain the external reported-usage stopping rule. Redesigned
additionally receives the same token ceiling as `TaskSpec.max_tokens` and may
reserve estimated usage internally, conservatively refusing requests earlier.
This behavior is part of the design being measured, not a precise billed-token
guarantee. Concurrent in-flight requests may exceed the external reported cap.
Redesigned shares step/turn limits and reserves parent integration capacity;
handoff_only retains the original child 12-step/12-turn caps. No arm receives
a higher external allowance or a cheaper model.

One attempt per arm/task, all episodes sequential. Arm order cycles through
the six permutations using each task's immutable dataset index. All failures,
provider errors and incomplete invocations remain recorded; the runner refuses
to restart an incomplete directory without explicit audit. Resume skips only
completed result files. Historical 40-run results are context, not replacements
for any of the new 78 episodes.

Existing independent scorers, immutable test restoration, dependency runtimes,
request diagnostics and whole-tree reconciliation are reused from
`evaluations/worker-code-20260928`. The previous 20 successful starter/reference
gates may be reused with their exact file hashes because task/scorer/dependencies
are unchanged. All six new tasks require starter failure and reference success
under those same scorers before model execution. Repository records are extracted
from the same pinned official parquet, with record and field hashes checked.
Oracle records remain private and outside every model workspace. Aider tests are
public but scoring uses an immutable copy. The original environment limitations
still apply: native-equivalent repository tests and portable Terminal scoring
are Aster adaptations, not official leaderboard scores.

Two holdout-only environment adaptations were found by preflight, before any
model result. BigCodeBench/30's two hard-coded `/tmp` fixture paths are relocated
to its isolated grading workspace without changing assertions. Django-13212
uses an independent hardlink clone of the existing Python3.9 environment with
the exact upstream CPython3.9.0 `urllib.parse` restored; modern bracketed-host
validation otherwise breaks the unchanged 2020 reference implementation.
Its unittest subTest failure summaries also restore full parent test IDs when
progress output joins adjacent test lines. Exact stdlib URL/hash are frozen in
`datasets/worker-code-v3-holdout/environment.json`. Both original failed gates
remain under private `preparation-attempts`; neither task was replaced.

Report regression and holdout results separately, including paired wins/losses,
independent task pass rate, whole-team input/output/cache tokens, end-to-end time,
model/tool calls, Worker completion and partial handoff, coordinator calls,
parent/child repeated file reads and missing usage. A completed Worker is not
itself a successful task. A smaller parent context alone is not lower team cost.
No confirmed relay pricing is available, so do not invent dollar savings.

Private raw output: `artifacts/private/worker-redesign-20260928`.
No paid model call occurs during `audit` or `preflight`.

```bash
 /home/dzh/.conda/envs/aster/bin/python evaluations/worker-redesign-20260928/prepare.py
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-redesign-20260928/evaluate.py audit
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-redesign-20260928/evaluate.py preflight
# bindings: {"solo":{"root":"/checkout","commit":"..."}, ...}
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-redesign-20260928/evaluate.py run --sources artifacts/private/worker-redesign-20260928/source-bindings.json
```
