# SymPy-14248: read-only delegation audit

This audit covers all **six completed SymPy-14248 episodes**: solo, current
and worker for repeats 01 and 02. All six stop at `team_token_limit`, all six
fail the original independent score, and none delivers correct normal work.
The three file-qualified upstream MatrixSymbol tests fail in every episode.
The worker and current arms each start one read-only child per repeat; all four
child runs stop before completion and deliver partial handoffs. No successful
independent contribution or delegation benefit is established.

This audit reads existing results, logs, saved requests, traces and source
differences only. It starts no diagnostic, candidate process, test or model
request, and changes no frozen input, product source or candidate workspace.
Supplementary public-expression diagnostics remain deferred until all 60 paid
episodes and grading finish.

## Evidence and outcome

All paths below are relative to the Aster repository:

- For repeat `NN` and arm `ARM`, `R = artifacts/private/worker-owned-20260930/repeat-NN/runs/swe-verified-sympy__sympy-14248--ARM` and `W = artifacts/private/worker-owned-20260930/repeat-NN/workspaces/swe-verified-sympy__sympy-14248--ARM`.
- Trace and saved requests: `W/.aster/runs/<run_id>/`.
- Base comparison: the already prepared issue-14248 `starter-workspace`
  under `artifacts/private/worker-code-20260928/repository-preflight/`.

`seq` refers to each run's JSONL trace sequence number; log line numbers refer
to `R/grading/tests.log`. Calls/tokens below include every parent and child.
Tool records include unsuccessful attempts. Wall time excludes independent
grading. Cache-read tokens are a subset of input tokens, not an additional cost.

| repeat | arm | parent / child run IDs | original pass | normal delivery | tokens | wall s | model / tool | children started / completed |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| r1 | solo | `00c682648f3143af929f662a72fe4b99` / — | no | no: team-token stop | 702,737 | 364.366 | 26 / 52 | 0 / 0 |
| r1 | current | `799a3ccb923b41d5a6bf502472a2e564` / `b14a871bbea24d47b98966ca3b5ff6a1` | no | no: team-token stop | 687,938 | 742.612 | 32 / 66 | 1 / 0 |
| r1 | worker | `0b1a76fd042a4fdb930238cac8eacacd` / `298a6a102d4a438986c307aa19a03c9a` | no | no: team-token stop | 701,768 | 361.409 | 31 / 71 | 1 / 0 |
| r2 | solo | `7a3982a52b2a42b5a1256a05657ea3aa` / — | no | no: team-token stop | 692,987 | 390.226 | 28 / 73 | 0 / 0 |
| r2 | current | `d5180f369e9041879f221435c5f2de43` / `f540f233b1fe4e31af77de03d2340933` | no | no: team-token stop | 703,376 | 530.433 | 31 / 68 | 1 / 0 |
| r2 | worker | `a4a237ed8dc0451aaa018831770abd28` / `ea7ee5bc5d884ef2b36a893e82450d22` | no | no: team-token stop | 692,298 | 370.367 | 31 / 67 | 1 / 0 |

| arm total | tokens | wall s | model calls | tool records | coordination calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| solo (r1+r2) | 1,395,724 | 754.592 | 54 | 125 | 0 |
| current (r1+r2) | 1,391,314 | 1,273.045 | 63 | 134 | 3 |
| worker (r1+r2) | 1,394,066 | 731.775 | 62 | 138 | 2 |
| all six | 4,181,104 | 2,759.412 | 179 | 397 | 5 |

The six episode totals include cache-read input and are not currency costs.
Runtime and ledger usage agree per episode. Child totals are 164,015 and
115,175 tokens for current r1/r2, and 80,405 and 84,870 for worker r1/r2;
the four child runs consume 444,465 tokens (10.63% of the six-episode total).
This is descriptive consumption, not proof that those tokens were wasted or
that the parent would otherwise have spent them.

The final parent verification state is `failed` for Solo r1, Worker r1,
Current r2 and Worker r2; it is `missing` for Current r1 and Solo r2. Each
grader reports 435 required names, zero missing names,
exit 1, and the same three requested file-qualified MatrixSymbol tests fail.
P2P failures vary in unrelated historical tests (r2 Solo has three; r2 Worker
has one); this does not change the three-printer result. Raw test totals remain
in each grading log and are not counts of satisfied requirements.

## Preserve printer file identity

The frozen historical parser (`official_log_parsers.py:219–246`) collects
file-qualified failure headings, but the required F2P/P2P mapping uses short
names. Its later loop assigns `test_status_map[test]` without retaining the
current file header. Same-named tests across printer files therefore collapse;
lines ending in `[FAIL]` also do not match its plain `" F"` suffix branch.
The unchanged original score must not be presented as three separate printer
coverage decisions. This audit instead reads the complete existing log with
each `sympy/...py[N]` file header intact.

| file-qualified test | observed status | log evidence |
| --- | --- | --- |
| `sympy/printing/tests/test_str.py::test_MatrixSymbol_printing` | failed | row 562; failure 698–701: exact public `str(A - A*B - B)` assertion |
| `sympy/printing/pretty/tests/test_pretty.py::test_MatrixSymbol_printing` | failed | row 140; failure 648–651: `pretty(A*B*C - A*B - B*C)` assertion |
| `sympy/printing/tests/test_latex.py::test_MatrixSymbol_printing` | failed | row 407; failure 678–681: first `latex(-A)` assertion |
| `sympy/printing/tests/test_str.py::test_MatrixElement_printing` | failed | row 561; failure 693–696 |
| `sympy/printing/pretty/tests/test_pretty.py::test_MatrixElement_printing` | passed | row 139 |
| `sympy/printing/tests/test_latex.py::test_MatrixElement_printing` | failed | row 406; failure 673–676 |

The other six code-printer MatrixElement rows also fail and remain in the
original log (C, Fortran, JavaScript, Julia, Octave and R). They are not merged
into the three requested printer rows above. In particular, the LaTeX test
stops at unary `-A` before its later public mixed-expression assertions.
This evidence establishes relevant formatting failures, but does not supply
the rendered public-expression text for all three printers. That separate
supplementary result is still pending.

## Assignment, handoff and parent use

At parent seq 159, the child receives `mode=explore` and an explicit no-edit
assignment: investigate MatrixSymbol subtraction, locate methods/tests,
recommend a minimal fix and exact regression cases. The declared parent work
is repository inspection, integration and focused/public-interface checks.

The child performs eight searches, fifteen file reads and one blocked shell
probe (seq 369, `approval_unavailable`). It changes no files and executes no
test or public-interface probe. At the 24-tool limit it returns a **partial**
handoff in model reply seq 937; its text exactly equals `handoff.json.answer`.
The report correctly identifies all three dedicated `_print_MatAdd` methods
and recommends extracting negative coefficients with `_coeff_isneg`. It
explicitly leaves exact runtime output and formatting verification unresolved.

Parent status seq 490 returns the full report. The first saved parent request
containing the exact decoded answer is:

`0011-737420002de669ed87d3b59d70d888d4a75deaff341035c8625b5d1a20402fbf.json`

The equality is checked after decoding `payload.input[52].output`, then its
`content`, then `answer`. This proves actual context availability, rather than
only a handoff file on disk. The child has already finished when status is
read; the recorded status duration is approximately 0.059 ms. The requested
`wait_seconds=300` is not 300 seconds of actual waiting.

After receipt, the parent searches the three `_print_MatAdd` locations
(504–510), reads them (529/532/535), and states the same fix direction in
reply 929. It then patches string (933), LaTeX (1090) and pretty (1336), adjusts
string spacing (1528), and adds a string regression test (1882). All four
changed files are retained in the final workspace. These edits are parent
work; the child has no retained code contribution. Receipt plus aligned edits
supports adoption of the suggested direction, but does not establish a unique
finding, a prevented defect or an advantage over direct work.

## Retained implementation and repeated investigation

All three actual matrix-add handlers are changed, so none of the three areas
is an implementation omission in this episode. Each is **implemented but
wrong** under the scoped upstream evidence above. The common defect is the
unchanged `_coeff_isneg` at `sympy/core/function.py:82–84`: it extracts a
leading coefficient only for `is_Mul`. Here `MatMul` inherits `MatrixExpr`,
sets `is_MatMul=True` (`matmul.py:15,29`), and retains the base
`is_Mul=False` / `is_Number=False` (`core/basic.py:74,76`). Negative matrix
terms therefore do not take the newly added sign-stripping branches.

Both agents search for this helper but stop their source reads before its
implementation: child seq 399 reads lines 45–75, and parent seq 594 reads
55–75. The search results contain only its definition at line 63. They see
documentation examples, not the decisive body at 82–84. No later read expands
that helper range. The child recommendation and parent implementation thus
share the same unverified assumption. The matrix-product printer bodies also
remain unchanged; LaTeX still joins every product argument, including `-1`.

There are **29 file reads and 23 searches** across the team, 52 of 66 tool
records. The parent reads seven distinct paths; all seven are also among the
child's nine read paths. Twelve of the parent's fourteen read calls overlap
at least one child-read line interval. This counts source-region overlap, not
proof that every reread was unnecessary. It does show that handing off the
investigation did not remove the parent's own source inspection. Parent and
child also attempt similar direct heredoc probes, both blocked; those blocked
commands supply no output evidence.

The parent's sole test run, seq 1583, actually executes pytest on the three
printer files and reports **7 failed, 306 passed**. Its full captured output
is in observation
`2b4dd2cf7c8baddc25bfdb856ce7306b37244ee0b470cb986ea8e7a814c0bcc6.json`.
The failures concern historical XFAIL handling and parser cases; this existing
test selection contains no new issue regression at that time. The parent adds
`test_MatAdd_subtraction` only at seq 1882, after that last run, and never runs
it. The grader restores candidate test modifications before applying the
original upstream tests. A failed historical test run, an unexecuted new test
and an independent failure are three different pieces of evidence; none is a
successful final verification.

## All-six file-qualified results and delegation records

The file-qualified MatrixSymbol status is identical in every completed
episode. The reported line differs only because the grader workspace and
whether the test uses `A-B` or `A*B*C-A*B-B*C` vary by run:

| episode | str `test_MatrixSymbol_printing` | pretty `test_MatrixSymbol_printing` | LaTeX `test_MatrixSymbol_printing` | raw test totals |
| --- | --- | --- | --- | --- |
| Solo r1 | failed, line 562 / assertion 703 | failed, line 140 / assertion 653 | failed, line 407 / assertion 683 | 497 passed, 12 failed, 5 XFAIL, 1 XPASS, 5 exceptions |
| Current r1 | failed, 562 / 698 | failed, 140 / 648 | failed, 407 / 678 | 498 passed, 11 failed, 5 XFAIL, 1 XPASS, 5 exceptions |
| Worker r1 | failed, 562 / 698 | failed, 140 / 648 | failed, 407 / 678 | 498 passed, 11 failed, 5 XFAIL, 1 XPASS, 5 exceptions |
| Solo r2 | failed, 562 / 718 | failed, 140 / 653 | failed, 407 / 698 | 494 passed, 15 failed, 5 XFAIL, 1 XPASS, 5 exceptions |
| Current r2 | failed, 562 / 708 | failed, 140 / 658 | failed, 407 / 688 | 496 passed, 13 failed, 5 XFAIL, 1 XPASS, 5 exceptions |
| Worker r2 | failed, 562 / 703 | failed, 140 / 648 | failed, 407 / 683 | 497 passed, 12 failed, 5 XFAIL, 1 XPASS, 5 exceptions |

The original parser exposes only the short F2P names and therefore cannot
attribute these failures to each printer. The table is an independent audit
of the existing logs; it does not rewrite the original score. In all six
episodes, the requested three printer areas are implemented but wrong under
the upstream exact checks. The unrelated P2P differences in Solo r2 and
Worker r2 remain separate from this printer conclusion.

### Worker r1

Parent todo 1 at seq 72 is “Inspect MatrixSymbol printer behavior and identify
the minimal shared printer fix”; the child is bound to this todo at seq 417.
The child run `298a6a102d4a438986c307aa19a03c9a` stops at its 24-tool limit
with `team_token_limit`, 80,405 tokens, no changes and no verification. Its
handoff is the deterministic generic partial record: it lists four read paths,
states no model conclusion was delivered, and leaves the todo pending. The
handoff is delivered at parent seq 2153 and appears verbatim in saved parent
requests 0025–0027; parent context therefore received it, though it contains
no substantive finding to consume.

The parent modifies `core/function.py` plus all three printer handlers, runs
focused tests and stops at the team budget. Its final workspace retains four
source files and no child edit. The child used 11 read/search records after
the parent had already begun source inspection; six read paths and 11 line
ranges overlap parent reads. This is measurable repeated inspection, while
the report does not support calling it wasted work.

### Worker r2

Parent todo 1 at seq 119 binds the child at seq 503 to “Fix MatrixSymbol
subtraction rendering across string, pretty, and LaTeX printers.” The child
`ea7ee5bc5d884ef2b36a893e82450d22` stops at step 24 with `step_limit`, then
the parent reaches `team_token_limit`; child usage is 84,870 tokens. It makes
no changes or tests and returns a substantive but partial report, including
the useful `MatMul.as_coeff_mmul()` direction. Todo 1 remains `pending` with
`delivery=partial` and no resolution in the parent checkpoint. The handoff is
automatically injected in parent request artifacts 0022–0026; later parent
source reads and edits align with its recommendation, but no independent
acceptance or successful child check exists.

The parent changes all three printer handlers and `test_str.py`; its final
verification is missing and the independent grader still fails all three
printer tests. Parent has 23 read/search records versus child 10 reads; six
paths and nine read ranges overlap. The parent also repeats the same sign
helper investigation after handoff. This overlap is descriptive, not a claim
that all repeated reads were unnecessary.

### Current r2

Parent starts a child at seq 235 without a todo binding (`todo_id` is absent),
so there is no explicit acceptance item to close. Child
`f540f233b1fe4e31af77de03d2340933` stops at step 24 with `step_limit` and
115,175 tokens, returns a substantive partial report, and makes no edits or
tests. The report is automatically present in parent request artifacts
0011–0026. Parent reply seq 711 explicitly restates its root-cause finding,
then changes the three printer handlers and the string test. Parent does not
complete its verification; the original grader fails all three printer tests.
Parent has 18 read calls versus child 11; six paths and seven read ranges
overlap. With no todo binding, the handoff has no recorded acceptance or
resolution beyond the parent's later edits.

### Current r1 (already detailed above)

The child `b14a871bbea24d47b98966ca3b5ff6a1` is explicitly bound to parent
todo 1, stops at step 24 with `step_limit` and 164,015 tokens, and delivers a
substantive partial report. Parent status seq 490 receives it; the exact
decoded answer appears in request `0011-737420002de669ed87d3b59d70d888d4a75deaff341035c8625b5d1a20402fbf.json`.
Parent then edits all three handlers and a string test but fails to run the new
test after adding it. The child has no source contribution and 12 of the
parent's 14 read calls overlap its ranges.

## Overall stage conclusion

Across all six completed printing episodes, every original score is 0/2 and
every arm stops at the team-token limit. Solo has no child overhead; current
and worker each add two partial, non-writing investigation children. Worker
uses 0.12% fewer tokens than Solo in aggregate and 3.0% fewer wall seconds,
but both arms have zero successful deliveries and the worker children do not
complete or verify. Current is slower because its two child episodes add
coordination and waiting. These descriptive differences cannot establish a
delegation benefit. The audit records four child partial handoffs, their todo
bindings and unresolved states, and the actual source/test outcomes without
calling partial reports successful work.

The frozen supplementary diagnostics remain pending until all 60 paid episodes
and independent grading results exist. No freeze or product file was changed.
