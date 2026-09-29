# SymPy-13878: delegation and twelve-distribution coverage

This static audit covers **all six completed episodes**. It reads finished
results, source, traces, saved requests and grading logs only; it does not
itself execute candidate code, tests or model calls. The separately scheduled
public-example diagnostics were subsequently run with a fixed offline script
and are reported below. They supplement this audit without changing the
original score or its coverage limits.

Worker passes the frozen independent score in 3/3 episodes versus Solo 1/3.
**Score pass plus normal delivery is Worker 2/3 versus Solo 0/3.** Solo r3
completes its internal verification but fails the independent score; this is
not a correct normal delivery. Worker r3 passes while omitting the requested precomputed
StudentT and UniformSum paths. Thus the original score cannot be treated as
complete twelve-distribution coverage. r1 has a demonstrable child code
contribution; r2/r3 use investigation children only, whose unique benefit is
not established by receipt of their reports.

## Evidence paths and outcomes

For repeat `NN` and arm `ARM`:

- `R = artifacts/private/worker-focused-20260929/repeat-NN/runs/holdout-sympy__sympy-13878--ARM`
- `W = artifacts/private/worker-focused-20260929/repeat-NN/workspaces/holdout-sympy__sympy-13878--ARM`
- Traces/requests/handoffs: `W/.aster/runs/<run_id>/`.
- Production/test source: `W/sympy/stats/crv_types.py` and
  `W/sympy/stats/tests/test_continuous_rv.py`.
- Frozen Aster commit: `856345eab891b327d61466e6c27b79d908be6b98`.

`seq` identifies a trace JSONL event. Log lines refer to `R/grading/tests.log`.
Tokens/calls include children. Wall time excludes subsequent grading. Tool
records include blocked/failed attempts, not just successful work.

| episode | frozen score | normal delivery | tokens | wall s | model calls | tool records | children |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| r1 Solo | fail | no: team_token_limit | 957,395 | 471.718 | 31 | 76 | 0 |
| r1 Worker | pass | yes: verified | 935,403 | 578.243 | 41 | 67 | 1 |
| r2 Solo | pass | no: team_token_limit | 953,566 | 567.008 | 33 | 79 | 0 |
| r2 Worker | pass | yes: verified | 953,075 | 583.194 | 43 | 111 | 2 |
| r3 Solo | fail | yes: verified internally | 890,540 | 475.597 | 30 | 65 | 0 |
| r3 Worker | pass | no: team_token_limit | 952,060 | 505.329 | 43 | 98 | 2 |

All six have 20 required names and zero missing required names. r1/r3 Solo's
only required failure is `test_arcsin`; all nineteen P2P tests pass. r1's failure
is a **symbolic Piecewise structural-equality assertion**, not an observed
incorrect value for the public numeric example (log 2576–2579). Its differently
ordered Piecewise branches must not be called numerically wrong without more
evidence. r3's final Arcsin also has a demonstrable static boundary defect,
described below; it must not be dismissed as merely a structural difference.

For all three pairs, Solo/Worker totals are **2,801,501 / 2,840,538 tokens
(Worker +1.39%), 1,514.324 / 1,666.766 s (+10.07%), 94 / 127 model calls
(+35.11%), and 220 / 276 tool records (+25.45%)**. The score improvement is
scoped to this fixed seen development task and its limited original oracle.
It is not a general success-rate estimate or a twelve-CDF correctness gain.
Cached input (Solo 825,344; Worker 1,498,880) is already included; token count
is not currency cost. Phase completion alone does not imply independent success.

| episode | parent run | child runs |
| --- | --- | --- |
| r1 Solo | `53d319a667094c5abc1a08b67342b096` | — |
| r1 Worker | `5f1e4913fa05476eba8fe53c63cba467` | `058b39cc148545c0be7c0d38ed53ee28` |
| r2 Solo | `a09af3f77f7d477c96d85412f0f60281` | — |
| r2 Worker | `18e940b7ff9246f5b9753388b8c2810d` | `eef8987775af4138b388ed68b61673ce`, `c22aa97bd5504129a96c7320a2dcbaee` |
| r3 Solo | `137c52ede946423f9a15dfc98ad71053` | — |
| r3 Worker | `67b38d97b0b54a8b9a0ae121fdc90735` | `b6149118e7df4db9bbe1dc9382f8c859`, `e5a5567d81de43549566663eabc5bf47` |

## Implementation versus behavioral coverage

All six add relevant CDF implementations. All Solo episodes and r2 Worker add
all eleven distribution-class `_cdf` methods, with Erlang using Gamma's path.
Only **r1 Worker** also fixes Erlang's `1/l` factory reciprocal to `S.One/l`.
The other five retain `1/l`; method presence therefore does not establish the
user's exact-input precision requirement. r3 Worker adds nine class methods,
leaving StudentT and UniformSum without `_cdf` or an alternative new CDF path.

Labels below concern the original distribution requirement, with scope stated:
`V` = concrete scoped behavioral evidence; `F` = observed incorrect behavior;
`U` = insufficient/confounded evidence; `O/U` = required precompute absent in
final source, with original runtime behavior still unmeasured. `V` is not an
exhaustive proof or necessarily an independent test. No unknown is dropped.

| distribution | r1 Solo | r1 Worker | r2 Solo | r2 Worker | r3 Solo | r3 Worker |
| --- | --- | --- | --- | --- | --- | --- |
| Arcsin | U | V | V | V | U* | V |
| Dagum | U | V | U | U | U | U |
| Erlang | F | V | U | U | U | U |
| Frechet | U | V | U | U | U | U |
| Gamma | U | U | U | U | U | U |
| GammaInverse | U | V | U | U | U | U |
| Kumaraswamy | U | V | U | U | U | U |
| Laplace | U | V | U | U | U | U |
| Logistic | U | U | U | U | U | U |
| Nakagami | U | V | U | U | U | U |
| StudentT | U | U | U | U | U | O/U |
| UniformSum | U | V | U | U | U | O/U |
| totals: V / F / U / O-U | 0 / 1 / 11 / 0 | 9 / 0 / 3 / 0 | 1 / 0 / 11 / 0 | 1 / 0 / 11 / 0 | 0 / 0 / 12 / 0 | 1 / 0 / 9 / 2 |

Evidence and limits behind the matrix:

- **Arcsin in r2 Solo/r2 Worker/r3 Worker:** V is scoped to the independent
  Arcsin CDF test, not an additional executed public-example diagnostic.
  r1 Solo's structural mismatch leaves numeric correctness U; a failing exact
  expression comparison is not proof of an incorrect probability.
- **r1 Worker:** final parent seq 2978 executes six child-edited test functions
  and one parent compound function, with 7 passed. Exact original points are
  asserted for Arcsin, Dagum, Erlang, Frechet, GammaInverse, Kumaraswamy,
  Laplace, Nakagami and UniformSum. Most expectations mirror implementation
  formulas and are agent-written, so mathematical-oracle independence is
  limited. Gamma and Logistic replace the original Python `0.1` with `S(1)/10`;
  those original float paths remain U. StudentT only asserts no `Integral`,
  not a correct finite probability or successful numeric evaluation.
- **r1 Solo:** its added twelve-case loop executes through all no-Integral
  checks but then fails Erlang exactness (parent seq 3289, again 3555 after
  changing the assertion to `simplify(...) == 0`). The unchanged factory and
  failed assertion demonstrate F for precision. No-Integral alone is not a
  correctness oracle for the other cases; later boundary assertions in that
  compound function were not reached.
- **r2 Solo:** its new original-example test block is only written at seq 5639,
  after the last test run. The earlier passing `test_precomputed_cdf` (5028)
  covers Normal, Pareto, ChiSquared and Exponential, not the twelve new paths.
- **r2 Worker:** the final “12 affected public tests passed” is mostly existing
  PDF/moment coverage. Its two passing CDF tests cover existing distributions;
  `test_difficult_univariate` concerns transformed Normal densities. These
  cannot validate the twelve requested CDFs. A new `.tmp/test_cdf_regression.py`
  is written at seq 3296 but its test run returns **file not found / no tests
  ran** at 3327. Its contents provide no behavioral evidence.
- **r3 Worker:** no parent `run_tests` or `run_shell` attempt occurs. Final
  StudentT and UniformSum source retains PDF-only classes. This proves the
  requested implementation omission, not a freshly observed hang or wrong
  numeric result. Other methods' existence is recorded separately from U.
- **r3 Solo:** final added test loops through all twelve rationalized cases,
  calls `N(value)` and checks only `is_number` and `[0,1]`. Parent seq 2994
  passes this test, and 3220 passes it plus the unrelated old precomputed test
  (2 passed / 61 deselected). There is no expected probability comparison,
  no Erlang exactness assertion, and Gamma/Logistic again use `S(1)/10`.
  Numeric evaluability is observed, but the matrix leaves correctness U.

### r3 Solo Arcsin: real static boundary defect, not just representation

The independent failure is at log 2638–2641, again comparing a symbolic
Piecewise CDF structurally. In this episode the final source also lacks the
zero-below-support branch: `_cdf` contains the arcsine formula only when
`And(a <= x, x <= b)` and otherwise returns `S.One`. Parent edit seq 1524
adds `a <= x` to the first condition but never adds `(S.Zero, x < a)`.
`crv.py:214` returns a supplied `_cdf` directly, without fixing its support.

Therefore **source inspection implies `a=0, b=3, x=-1` selects 1 rather than
the required 0**. No boundary runtime check was executed by this auditor;
the `U*` records pending behavioral checking of the original interior example
alongside this separate static defect. Even an interior sample pass cannot
establish correct support handling. The agent's final statement that it added
the Arcsin lower bound overstates what the change achieved. Its successful
test checks only the interior point 1, so it cannot catch the missing branch.

## Actual child contribution and consumption

All five started children stop at their 24-tool limit and deliver **model-
authored partial** reports; each `model_reply.text` exactly matches its saved
`handoff.json.answer`. None completes normally. This does not erase retained
code contribution in r1, nor does report delivery establish a contribution in
r2/r3.

| Worker repeat | real assignment and output | parent consumption and contribution limit |
| --- | --- | --- |
| r1 | One writing worker owns Arcsin, Dagum, Frechet, Kumaraswamy, Laplace, Logistic; adds six `_cdf` bodies and six point assertions. All six final method bodies exactly match additions in child seq 695; 249,700 child tokens. | Status seq 675; parent reads integrated diff (684), adds remaining methods (1261), runs six delegated tests successfully (1724), acknowledges them (2154), then retests final source. **Direct retained-code contribution established.** |
| r2 | Two `explore` workers: seven distributions and five distributions. No edits or runtime tests; 85,362 + 66,269 tokens. StudentT naming and UniformSum boundary details remain incomplete in the reports. | Status seq 487 delivers both; parent then makes 33 file reads overall, including all class groups after receipt (497/519/542), and authors every implementation. Final formulas align with report guidance, but unique findings or a prevented defect are not established. |
| r3 | Two `explore` workers cover six and four distributions; no edits or executed checks; 70,169 + 103,616 tokens. A requested third worker for StudentT/UniformSum **fails to start** at seq 162 with `worker_limit`. | Status seq 343 delivers the two reports; parent rereads their class regions (355) and authors methods. No replacement delegation follows; StudentT/UniformSum remain absent. Report receipt does not establish coverage or successful contribution for the missing group. |

Full-answer equality in the first later parent requests was checked after
JSON decoding; these are availability evidence, not merely handoff files:

| repeat | saved request | exact answer location |
| --- | --- | --- |
| r1 | `0011-80824f6a27f697590e1a59915cdea66571279b3194efd79f18bb1c6be5b7b094.json` | decode `payload.input[49].output`, then `content`, then `answer` |
| r2 | `0009-518faec2a500e3282820dd7de57872f70860ea0777ecc1c473382e4cd4ab4d29.json` | decode input 38 output/content; `workers[0/1].answer` |
| r3 | `0009-aa2a27cc715c5d5cbf18bd6dd7504bcd38c2f62e04f746e4fca278282f02d7bb.json` | decode input 56 output/content; `workers[0/1].answer` |

The r1 parent attempts a same-file edit during the child's write phase and is
blocked by `worker_writer_active` (649), then waits 117.326 s. Its investigation
can overlap the child, but edits serialize. The r2/r3 status waits are 101.331 s
and 56.332 s. These waits and repeated source reads preclude claiming a proven
parallel-editing latency win from this evidence.

Reports also contain errors that consumption did not automatically resolve.
The r1 child calls five test failures existing/XFAIL-related, but its new
Arcsin assertion actually raises missing-`asin` `NameError` (child 1433). It
fixes that import (1728); later parent tests establish the fix. The r2 first-
group report says adding Gamma `_cdf` avoids Erlang float behavior while its
factory remains `1/l`; this precision requirement is not actually repaired.
The r3 first-group report gives a false alternate Dagum “equivalent” expression;
the parent uses the correct ratio form, so that erroneous alternative is not
in the final method. Reports are fallible evidence, not correctness gates.

## Verification and scoring boundaries

Only r1 Worker has passing final point assertions against expected CDF values
for the nine original examples scoped above. Its
parent additionally repairs Erlang precision after failed seq 2229 (edit 2407),
and UniformSum's unevaluated `Sum` after failed seq 2482. Final focused seq 2978
reports 7 passed; broader seq 2992 reports 57 passed / 6 deselected on workspace
hash `89638b72c78ae68127a62a386af5332ae4ec913a7b4c3b2be9b7cf6785bc027a`.
These repairs are parent work. The newly added assertions contain no explicit
support-boundary checks despite the final response's broader claim.

r2 Worker completes after passing existing filtered tests, while r3 Worker runs out
of tokens after repeated patch matching/fresh-read failures and never verifies.
The r2 final response's claim that the full suite's only issue is UniformSum
XFAIL is too broad: its full native run seq 2625 has runtime/setup exceptions;
only its narrower pytest selection reports UniformSum as the sole failure.
Heredoc probes blocked by `approval_unavailable` are not executed checks.

The frozen independent requirement is **test_arcsin + 19 P2P**, not twelve CDF
behaviors. Candidate test modifications are restored before grading. All six
grading commands exit 1: r1/r3 Solo log 20 passed, 1 failed, 2 skipped, 4 expected
failures and 36 exceptions; the four score-passing episodes log 21 passed,
2 skipped, 4 expected failures and 36 exceptions. Many non-scoring exceptions
come from historical `collections` deprecation warnings promoted to errors,
before individual CDF assertions are reached. They must neither be hidden
nor counted as twelve algorithmic failures.

## Supplemental public-example diagnostics

The diagnostics are stored in the six CDF result files below. They use the
same fixed three-point sample for each distribution and report symbolic
expression shape, float atoms, unevaluated integrals and numeric range. They
are a public-example smoke check, not the frozen grader and not a proof of
all-parameter or support-boundary correctness.

- Solo: `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-01--cdf--solo.json`, `repeat-02--cdf--solo.json`, `repeat-03--cdf--solo.json`.
- Worker: `artifacts/private/worker-focused-20260929/public-diagnostics/repeat-01--cdf--worker.json`, `repeat-02--cdf--worker.json`, `repeat-03--cdf--worker.json`.
- Script SHA-256: `5000f20a34cf3888898c12b5258ab64e25b6043e9829a1b1350b7a3ae80d2435`.

| episode | verified sample points | observed mismatch | unknown |
| --- | ---: | --- | --- |
| Solo r1 | 10/12 | Erlang, StudentT | 0 |
| Worker r1 | 12/12 | 0 | 0 |
| Solo r2 | 10/12 | Erlang | UniformSum |
| Worker r2 | 10/12 | Erlang | UniformSum |
| Solo r3 | 10/12 | Erlang | UniformSum |
| Worker r3 | 9/12 | Erlang | StudentT, UniformSum |
| **Solo total** | **30/36** | **4 cases** | **2 cases** |
| **Worker total** | **31/36** | **2 cases** | **3 cases** |

Across the three repeats, Solo has 30 verified distribution-point checks, four
observed mismatches and two unknown distribution cases; Worker has 31 verified,
two mismatches and three unknown cases. These are episode-level distribution
statuses across 36 cases per arm; an unknown case is retained as unknown and is
not counted as a mismatch. The two r2 UniformSum results are unknown in both
arms, while Solo r3 has one unresolved UniformSum case and Worker r3 has
unresolved StudentT and UniformSum cases.

The important observations are:

- Erlang is a mismatch for Solo r1/r2/r3 and Worker r2/r3 because the result
  contains a Python float (`0.632120558828558`). Worker r1 returns the exact
  symbolic `-exp(-1) + 1`, consistent with its parent repair of `1/l` to
  `S.One/l`.
- Solo r1 StudentT is a mismatch: the negative and positive probes do not
  exhibit the expected CDF symmetry (`x=-2` produces the same value as the
  positive-side result). Solo r2/r3 and Worker r1/r2 pass the sampled StudentT
  checks; Worker r3 timed out and remains unknown.
- UniformSum passes in Solo r1 and Worker r1 after integer finite-sum
  expansion. Solo r2 and Worker r2 return an unevaluated `Sum` and are unknown;
  Solo r3 and Worker r3 have no usable observation and are also unknown.

These checks sample interior points only. In particular, they do not detect
the r3 Solo Arcsin below-support defect documented above: its `_cdf` still
falls through to `S.One` for `x < a`. A verified interior value therefore does
not erase that static finding. Likewise, a verified sample point is scoped
evidence rather than a claim that the entire distribution implementation is
correct. The original observed benefit remains **score pass plus normal
delivery in Worker 2/3 versus Solo 0/3, with one concrete child
implementation contribution**; time and total tokens do not improve, and the
public diagnostics do not establish all-twelve correctness.

Internal completion, original scoring, the supplementary sample diagnostics
and requirement coverage remain distinct measures. The diagnostics add
evidence for selected points; they do not relabel any U/O-U entry or turn the
limited oracle into complete twelve-distribution validation.
