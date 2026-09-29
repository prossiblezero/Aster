# SymPy-14248: actual delegation and printer coverage

All six printing episodes are complete. Both arms passed **0/3** frozen
independent evaluations and delivered **0/3** normal final answers. Worker
used 1.7% fewer total tokens but 16.6% more wall time, 16.0% more model calls
and 28.5% more tool calls. All six stopped at the team-token budget. This is
not persuasive evidence of a useful token saving: no episode solved the task.

Each Worker episode started one read-only investigation child. All three
children produced model-authored **partial** handoffs at their 24-tool step
limit, with no edits. All three reports reached a later parent model request;
that establishes receipt, not unique contribution or correctness. In r3 the
parent still missed the pretty printer's actual MatAdd entry point despite
the child's report identifying it.

This audit only reads finished results, traces, requests and source files; it
does not itself execute candidate code, diagnostics, tests or model calls. The
separately scheduled public-example diagnostics were subsequently run with a
fixed offline script and are reported below. Nothing in this document changes
the original score, frozen input or task selection.

## Evidence map

For repeat `NN` and arm `ARM`:

- `R = artifacts/private/worker-focused-20260929/repeat-NN/runs/swe-verified-sympy__sympy-14248--ARM`
- `W = artifacts/private/worker-focused-20260929/repeat-NN/workspaces/swe-verified-sympy__sympy-14248--ARM`
- A run's trace, saved requests and child handoff are under `W/.aster/runs/<run_id>/`.
- Grading lines refer to `R/grading/tests.log`; `seq` refers to a trace event's
  JSONL `seq`, not its model turn.
- Frozen Aster source commit: `856345eab891b327d61466e6c27b79d908be6b98`.

| repeat | Solo run | Worker parent | Worker child |
| --- | --- | --- | --- |
| r1 | `95a809a08bf04e78883f102f9b0f9687` | `61c26fe95e584cf78ea9a8cad5c01a15` | `07dfb1f19b314b178b0715ae30f3f96a` |
| r2 | `f5410ad886284599b6888a3becc83214` | `7993a521c29944279c1988aa03c1f677` | `41e9399990354208874241835ce9045c` |
| r3 | `aab3688c73e94117b8354ef1dd523827` | `cfe630e77e904f00ae54fd9058d516f8` | `36081241fd744685a6cbd0aea549fecd` |

## Paired episode outcomes

Every episode has `passed=false`, `phase=stopped`, `stop_reason=team_token_limit`,
435 required test names and zero missing required names. Both arms use the same
750,000-token team budget; a pre-request budget stop need not consume exactly
that amount. Wall time excludes subsequent independent grading. Token totals
include all children and all observed input/output, including cached input.

| repeat | arm | team tokens | wall s | model calls | tool-result records | child tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| r1 | Solo | 713,510 | 374.995 | 27 | 49 | 0 |
| r1 | Worker | 694,653 | 543.938 | 30 | 79 | 107,210 |
| r2 | Solo | 695,747 | 350.343 | 27 | 49 | 0 |
| r2 | Worker | 692,029 | 354.114 | 30 | 63 | 132,565 |
| r3 | Solo | 703,523 | 373.843 | 27 | 60 | 0 |
| r3 | Worker | 690,565 | 383.991 | 34 | 61 | 199,942 |
| sum | Solo | 2,112,780 | 1,099.181 | 81 | 158 | 0 |
| sum | Worker | 2,077,247 | 1,282.043 | 94 | 203 | 439,717 |

Worker/Solo paired token differences are −2.64%, −0.53%, −1.84%; wall-time
changes are +45.05%, +1.08%, +2.71%. Aggregate percentages use sums, not an
average of percentages. Tool-result records include failed/blocked attempts;
these are work/coordination counts, not successful task steps. Cached input is
753,664 tokens in Solo and 788,992 in Worker and must not be added again.
Token count is not currency cost. With all episodes stopped and unsuccessful,
neither small token differences nor fewer parent-only calls demonstrate
useful completed-work efficiency.

## Requirement coverage and labels

The public request names string, pretty and LaTeX printing of matrix
subtraction. The two short F2P names aggregate duplicate names across files;
therefore labels below use the raw file-qualified failures plus inspected
implementation paths. A file modification by itself does not establish
coverage. `implemented_but_wrong` means a relevant change exists and observed
printing behavior fails a scoped check; it does not mean every input fails.

| episode | string | pretty | LaTeX |
| --- | --- | --- | --- |
| r1 Solo | implemented_but_wrong | implemented_but_wrong | implemented_but_wrong |
| r1 Worker | implemented_but_wrong | implemented_but_wrong | implemented_but_wrong |
| r2 Solo | implemented_but_wrong | implemented_but_wrong | implemented_but_wrong |
| r2 Worker | implemented_but_wrong | implemented_but_wrong | implemented_but_wrong |
| r3 Solo | implemented_but_wrong | implemented_but_wrong | implemented_but_wrong |
| r3 Worker | implemented_but_wrong | omitted | implemented_but_wrong |

At this printer-area scope: Solo has **0 verified / 9 implemented_but_wrong /
0 omitted / 0 unknown**; Worker has **0 / 8 / 1 / 0**. These are three fixed
areas repeated three times, not nine independent tasks. The labels include
negative-matrix formatting checks; they do **not** assert that the exact
public mixed-product expression was independently executed for every printer.
That narrower public-example coverage is explicitly limited below.

File-qualified independent evidence:

- In r1 Solo, r2 Solo, r2 Worker and r3 Worker, the same short log layout shows
  `pretty/tests/test_pretty.py:test_MatrixSymbol_printing` failing on
  `pretty(A*B*C - A*B - B*C)` at lines 648–651; `tests/test_latex.py` fails
  its first `latex(-A) == r"-A"` assertion at 678–681; `tests/test_str.py`
  fails the exact public `str(A - A*B - B)` assertion at 698–701. The LaTeX
  test stops before its later mixed-expression cases. Raw totals are 498
  passed, 11 failed, 5 expected failures, 1 unexpected pass and 5 exceptions.
- r1 Worker: string enters the new `_print_MatAdd → _print_Add → _print(term)`
  recursion on the exact public expression (34376–36355). Pretty already
  recurses on `A - B` (4603–6584). LaTeX recurses on the existing `C - 2*B`
  regression (16513–18496), and the new test separately fails `latex(-A)`
  (36358–36361). Raw totals: 490 passed, 1 failed and 23 exceptions, plus
  the same 5 expected failures and 1 unexpected pass.
- r3 Solo: pretty recurses on `A - B` at 4603–6584; LaTeX fails `latex(-A)`
  at 6631–6634; string fails the exact public expression at 6651–6654.
  Raw totals: 495 passed, 11 failed and 8 exceptions, plus the same expected
  and unexpected-pass counts.

Those aggregate test counts are not counts of satisfied task requirements.
An assertion mismatch alone does not identify whether the failure is ordering,
spacing or signs. Source inspection supplies the relevant implementation
limitations, while actual rendered final strings are summarized by the
supplemental diagnostics below:

- r1 Solo, r2 Solo and r2 Worker implement `_print_MatAdd` sign checks using
  `_coeff_isneg`, but leave that helper unchanged. Its body only extracts a
  coefficient for `is_Mul`; this base's `MatMul.is_Mul` is false. r3 Worker's
  pre-edit runtime probe (parent seq 563) confirms `_coeff_isneg(-B)` and
  `_coeff_isneg(-A*B)` are false on that same unchanged base behavior. Merely
  copying this scalar helper does not provide matrix sign handling.
- r1 Worker directly delegates all three matrix-add handlers to scalar Add,
  which is incompatible with term ordering here and recurses as the logs show.
- r3 Solo changes the shared sign helper to recognize MatMul and attempts
  further printer fixes, but leaves pretty delegated to scalar Add. Its
  LaTeX handler prints the first negative MatMul unchanged; the matrix-product
  printer still emits every coefficient. This is relevant incomplete sign
  handling, not an untouched printer file.
- r3 Worker changes string/LaTeX sign detection to `is_MatMul`, discovers that
  unary negation can leave nested `-1` factors, and changes those two handlers
  to rebuild the product's coefficient. These final edits were not re-probed
  before the budget stop; independent grading still fails both printers.

### Why r3 Worker pretty is an omission

Parent seq 1528 changes **scalar `_print_Add`**, adding an `is_MatMul` branch.
The actual `_print_MatAdd` at `pretty/pretty.py:821` remains exactly the original
`return self._print_seq(expr.args, None, None, ' + ')`. Its matrix multiplication
handler also remains unchanged. The new scalar branch is not on the public
MatAdd printing path.

After that edit, parent seq 1614 successfully executes the public expression
and records `-B + -A⋅B + A` for pretty. There are no later pretty edits; subsequent
changes affect only string and LaTeX. Thus a relevant file was touched but
functional coverage of the requested pretty subtraction path was omitted.
This label is based on the unchanged dispatch path **and** executed output,
not merely a failed test or the presence/absence of a changed file.

For exact public-example evidence, the independent string assertion runs in
all six episodes. r3 Worker's pretty output is also directly observed after
its last pretty edit. Several pretty tests stop on other subtraction examples;
all LaTeX new tests stop at unary `-A`. Their final exact public-expression
outputs remain unknown where the supplemental diagnostic could not observe a
result, and observed strings remain `unknown` where equivalence needs manual
inspection. The source analysis above must not be relabeled as an executed
diagnostic.

## What the workers actually contributed

All children use read-only `explore`, are stopped at 24 tools, have
`changed_paths=[]`, and report `delivery=partial`. Their final `model_reply`
text exactly equals `handoff.json.answer`, so these are genuine model-authored
reports rather than deterministic fallbacks. No child completes normally.

| repeat | actual assignment and delivery | later parent evidence | contribution assessment |
| --- | --- | --- | --- |
| r1 | Inspect MatAdd, all three printers, regression expectations and risks; child seq 724; 10 searches + 14 reads; no runtime checks. | `worker_status` seq 629 returns report; parent request 0010 input 65 contains the full answer; reply seq 709 explicitly says the investigation confirms the fix; edits seq 838/979/1072 apply direct Add delegation. | Receipt/adoption proven, but parent seq 460 already states the same root cause. Six of seven child-read paths were also read by parent before receipt. No unique finding or caught defect established. |
| r2 | Locate matrix printer implementations/tests and recommend fix; child seq 781; 12 searches + 11 reads + one listing; no runtime checks. | No status call, but **automatic handoff injection** puts the full report in parent request 0011 input 62 before edits seq 793/984/1263. | Context receipt proven. The same `_coeff_isneg` recommendation appears in final edits, but parent seq 286 already identified the cause and seq 312 read the scalar helpers. A distinct causal contribution is not established. |
| r3 | Investigate source/tests, current behavior and minimal fix; child seq 1172; 8 searches + 14 reads + blocked shell + failed pytest. | Status seq 299 waits 72.460 s and returns report; parent request 0007 input 18 contains it. Reply seq 357 explains MatAdd's separate path and then reads identified handlers. | Useful source-location information was available before parent file reads, but parent repeats 15 reads overall. Child's recommendation to use `_coeff_isneg` is incompatible with MatMul; the parent discovers that itself in probe seq 563. The final pretty-path omission remains. |

Exact first receiving request artifacts:

- r1: `0010-d4f89a5e714cbccc70350872c59575287a1ff739f3eabae5b1a2ee5e315ee9a2.json`;
  decode `payload.input[65].output` and its `content` JSON to recover the answer.
- r2: `0011-7ab0ccf691ef8fd3cd68e7c3d7d9c64b9c6df87205b7824f39043bc83bc62005.json`;
  `payload.input[62].content` is a `Worker handoff` user message containing JSON.
- r3: `0007-4e90739320b431db3fa5793c3b575323b910123528bd925d24cfbb373103a676.json`;
  decode `payload.input[18].output` and its `content` JSON.

In all three, decoded `answer` exactly equals the corresponding saved child
handoff. Reports in a later request prove availability; r1's explicit
acknowledgement is stronger adoption evidence than r2's mere aligned edit.
None establishes an independent successful contribution or a counterfactual
benefit over Solo.

The r3 child did execute `python -m pytest` on the **unchanged base**, which
failed. Its handoff describes four failures involving historical XFAIL handling
and parser cases; no child runtime output of the exact public expression was
obtained, because its inline-shell probe was blocked. This is baseline test
information, not verification of the parent's later edits. The child report
names all three correct MatAdd handlers, but the parent edits the wrong pretty
handler anyway.

## Supplemental public-example diagnostics

The six supplemental result files are:

- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-01--printing--solo.json`
- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-01--printing--worker.json`
- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-02--printing--solo.json`
- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-02--printing--worker.json`
- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-03--printing--solo.json`
- `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-03--printing--worker.json`

They use the same fixed expression `A - A*B - B` for `str`, `pretty` and
`latex`. The diagnostic script SHA-256 is
`5000f20a34cf3888898c12b5258ab64e25b6043e9829a1b1350b7a3ae80d2435`.
Because symbolic/formatting equivalence cannot be decided reliably by the
script, `unknown` is retained as a distinct result and is not counted as a
failure.

| episode | str | pretty | latex |
| --- | --- | --- | --- |
| Solo r1 | unknown (`(-1)*B + (-1)*A*B + A`) | verified_public_example (`-B-A⋅B + A`) | unknown (`-1 B + -1 A B + A`) |
| Worker r1 | unknown (no observation; exit 1) | unknown (no observation; exit 1) | unknown (no observation; exit 1) |
| Solo r2 | unknown (`(-1)*B + (-1)*A*B + A`) | unknown (`-B + -A⋅B + A`) | unknown (`-1 B + -1 A B + A`) |
| Worker r2 | unknown (`(-1)*B + (-1)*A*B + A`) | verified_public_example (`-B-A⋅B + A`) | unknown (`-1 B + -1 A B + A`) |
| Solo r3 | unknown (`(-1)*B - (-1)*(-1)*A*B + A`) | unknown (no observation; exit 1) | unknown (`-1 B - -1 -1 A B + A`) |
| Worker r3 | unknown (`-1*B - 1*A*B + A`) | unknown (`-B + -A⋅B + A`) | unknown (`-1 B - 1 A B + A`) |

Solo has **1 verified result and 8 unknown results**; Worker has **1 verified
result and 8 unknown results**. The two verified results are r1 Solo pretty
and r2 Worker pretty. The observed strings that contain explicit `-1` factors
are preserved as evidence of remaining formatting problems, but are not
reclassified automatically because the checker does not prove algebraic
equivalence. This diagnostic therefore adds concrete rendered-output evidence
without changing the original 0/3 frozen score for either arm.

## Verification and workflow limitations

| episode | execution observed in parent trace | final verification state |
| --- | --- | --- |
| r1 Solo | Heredoc shell probe blocked (seq 314); four native test calls, none successful, including failing added string test (2933). | failed |
| r1 Worker | Only inline/heredoc shell probe blocked with `approval_unavailable` (603); no `run_tests` call. | missing |
| r2 Solo | Heredoc probe blocked (301); direct pytest reports failures (1141); later edits are not successfully verified. | missing |
| r2 Worker | Heredoc probe blocked (1363); native printer test run fails (1829). | failed |
| r3 Solo | Heredoc probe blocked (497); native tests fail (1995); unsupported `--match` option fails (2180); later `python -c` probe raises during pretty printing (2293). | failed |
| r3 Worker | Three successful `python -c` probes (563, 665, 1614); last reveals broken formatting. Subsequent string/LaTeX repairs are unverified; no parent `run_tests`. | missing |

Several native runs report `test_setup_or_teardown_failed`, while their stored
output shows tests actually ran and includes historical parser/runtime errors.
That tool reason must not be turned into “no tests executed.” Conversely a
successful `run_shell` only means the command ran; r3 Worker's successful
probe visibly prints incorrect output. Candidate test edits are discarded
by independent grading, which applies the frozen tests in a separate copy.

The child/parent investigation overlap, partial handoffs, incorrect helper
reuse, blocked heredoc probes and uncompleted verification all matter when
interpreting this task. The reports did not prevent a wrong-path edit or
produce a successful delivery. Under this frozen setup, printing supplies
**no persuasive delegation benefit** in task pass, completed-work efficiency
or requirement coverage. This conclusion is scoped to these three paired
repeats of one seen development task, not all delegation workloads.
