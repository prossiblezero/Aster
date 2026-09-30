# Owned Worker: fixed mixed-source development regression, 2026-09-30

The frozen external selection is `datasets/worker-owned-20260930/selection.json`:
10 previously evaluated tasks, **3 simple / 5 medium / 2 hard**, five sources.
This is development regression after observing earlier outcomes, not a holdout
or a representative leaderboard sample. No task replacement or early success
stopping is allowed. Task instances, prompts and reused gates are linked by SHA-256.

The design is **10 tasks × 3 arms × 2 fresh repeats = 60 model episodes**:

| arm | source | max_workers | interpretation |
| --- | --- | ---: | --- |
| solo | new bound product commit | 0 | new single-agent system |
| current | `6ccb8be1ef994f490eaa79a3786c0fb9c18df7e4` | 2 | previous delegated system |
| worker | exactly the same source/root as solo | 2 | new delegated system |

Only **new solo versus new worker** isolates Worker availability. Current versus
worker is a before/after system comparison, including all product changes; it
does not alone identify the causal contribution of delegation. Both repeats are
independent **pass@1** samples, never pass@2, best-of-two, retries or two chances
to succeed. Preserve every cancellation, budget stop, failure and grader error.

The selected JSON contains each task's two arm orders. All six permutations
occur three or four times across 20 blocks; each arm occupies each order
position six or seven times. Both repeats for a task have different orders.
Episodes are serial in task order, then repeat order, then the stored arm order,
with fresh processes and fresh workspaces. Resume skips complete existing results
and refuses partial episode directories; no performance-driven rerun is allowed.

All arms use identical original prompts, the unchanged legacy neutral suffix,
public adapter environment guidance, model, endpoint, context/output settings,
memory-off settings and prepared dependencies. No selection rationale, suggested
decomposition, hidden test, solution or supplemental diagnostic is model input.
The arms choose whether/how to delegate; delegation is not forced. Child requests,
tools and reported tokens count against the same **whole-team** budget:

| stratum | model requests | tools | reported tokens | seconds |
| --- | ---: | ---: | ---: | ---: |
| simple | 40 | 60 | 500,000 | 600 |
| medium | 60 | 90 | 750,000 | 900 |
| hard | 80 | 120 | 1,000,000 | 1,200 |

`TaskSpec.max_tokens` applies the same internal team budget to all three arms.
Internal estimated request/output reservations can stop admission below the
reported ceiling. No arm receives extra team resources or a different child model.
The run lock freezes bindings, product hashes per arm, settings, endpoint hashes,
Python/package environment hashes, selected gates and explicit driver files.
Source imports must resolve to the bound checkout under Conda `aster` Python 3.12.
The independent grader's shared Aster testing/runner/workspace support must remain
byte-identical across bound sources. New diagnostics files are excluded from the
paid controller's explicit file set and must have their own frozen protocol.
The supplemental `diagnostics/freeze.json` must exist before paid launch; its
SHA-256 is stored and checked by the main run lock for every episode.

Preparation, original independent scoring and HTTP ledger reuse the existing
`worker-code-20260928` / `worker-redesign-20260928` adapters unchanged. All ten
starter-fail/reference-pass gates are reused only after hash and count checks.
Required counts in selected order are **20, 12, 5, 54, 62, 435, 7, 3, 8, 20**.
Scoring restores immutable tests. Agent-authored tests may provide runtime evidence
but never replace independent assertions. No benchmark shell is added to exam_project.

Primary outcomes are frozen independent task pass over **all 20 episodes per arm**,
pairwise wins/losses/ties and **correct normal delivery**: independent passed AND
phase=completed AND stop_reason=verified AND no episode error. Internal verified
is not independent correctness; a correct patch after a budget stop is not normal
delivery. Report each task's two outcomes, difficulty/source strata and all failures.
Do not pool subtests into a substitute task pass rate.

Costs include every parent/child input, output, cached input, reasoning, reported
total token, model/tool call, coordination call and wall second (excluding independent
grading). Cached input and reasoning are subsets, not extra tokens to add twice.
List HTTP attempts without usage separately: their usage/currency cost is unknown.
Reconcile parent and child usage independently, and retain unknown HTTP
classifications alongside all-episode totals.
No confirmed endpoint price is available, so report tokens rather than invented dollars.
Report all episodes first; then each arm pair's both-passed and both-correct-normal
subsets, and the corresponding all-three-arm subsets. Empty subsets stay empty.
These success-conditioned subsets are descriptive and do not replace primary totals.
Actual-worker-use subgroups are post-hoc descriptions, not randomized comparisons.

Manual contribution requires assignment, actual child action, partial/full report,
consumption in a subsequent parent request and a resulting edit/check/decision.
Receipt or child completion alone is not benefit. Keep this evidence separate from
automatic aggregate scores. Supplemental public-example diagnostics run only after
all paid episodes settle and remain separate from frozen independent scores.

Limitations inherited from the unchanged adapters remain visible: Terminal-Bench
is portable secondary scoring, not official Docker scoring; gRPC replays submitted
server artifacts and does not evaluate cross-tool daemon persistence; COBOL uses
the original fixed-output assertions without a live compiler differential. Repository
scores are native-equivalent adaptations. SymPy printing has cross-file short-name
collisions; CDF scores Arcsin plus 19 regressions rather than all twelve requested
distributions. A frozen pass can coexist with non-scoring test failures. Neither
internal test counts nor supplemental checks silently change those score definitions.

```bash
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/checks.py
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/evaluate.py audit
# After the product commit and isolated source bindings exist:
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/evaluate.py audit --sources /path/to/bindings.json
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/evaluate.py run --sources /path/to/bindings.json
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python evaluations/worker-owned-20260930/summarize.py
```

`audit` performs no model call and launches no benchmark test. `run` is the only
controller path that starts paid episodes. Outputs live in
`artifacts/private/worker-owned-20260930/repeat-01` and `repeat-02`; commit the
curated reports and aggregate records, while requests/traces/workspaces remain private.
