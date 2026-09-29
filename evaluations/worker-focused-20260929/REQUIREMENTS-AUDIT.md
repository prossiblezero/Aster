# SymPy requirement coverage: audit boundaries

This document is an audit aid, not model input or a change to the independent
scorer. It records what can be concluded from the reused oracle before the
focused paid run. Task and gate hashes are in the dataset selection. All new
episode-level manual labels begin as `unknown`.

## SymPy-14248: three printers

The original request names `str`, pretty printing and LaTeX rendering of
MatrixSymbol subtraction. The requirement is correct behavior in all three
printers, including the supplied expression; updating one printer does not
establish coverage of the others.

| area | evidence to inspect | what the frozen scorer does not establish alone |
| --- | --- | --- |
| string | public original example, string-printer diff, tests from `test_str.py` | passing `test_MatMul_MatAdd` alone does not cover the whole new behavior |
| pretty | public original example, pretty-printer diff, tests from `test_pretty.py` | passing the old `test_MatrixExpressions` regression alone is insufficient |
| LaTeX | public original example, LaTeX-printer diff, tests from `test_latex.py` | passing old `test_matAdd`/`test_matMul` regressions alone is insufficient |

The original hidden patch adds `test_MatrixSymbol_printing` in all three
printer test files, while `test_MatrixElement_printing` also exists in several
code-generator printer files. The original required F2P list contains only
those two **short names**. The inherited official parser can collapse repeated
names, so neither F2P status identifies each printer independently. The full
grading log retains file context; manually inspect that context and the
candidate behavior instead of attributing a short-name result to every file.
Full-path failures in the diagnostic dictionary may help identify failures,
but the absence of a full-path failure is not proof that the corresponding
test ran and passed.

Historical examples confirm why this matters: the earlier redesigned episode
had a string-printer recursion error and a LaTeX assertion failure in distinct
file-qualified log sections, despite only two aggregated F2P names. Those
earlier outcomes do not supply labels for the new repeats.

## SymPy-13878: twelve named distribution requirements

The original request explicitly lists the following CDF cases. The public
examples and their described failure modes, not the reference implementation,
define the manual checklist.

| requested distribution | original symptom or expectation |
| --- | --- |
| Arcsin | incorrect unevaluated/nonconvergent integral |
| Dagum | evaluation hangs |
| Erlang | exact inputs should not unnecessarily become a float |
| Frechet | evaluation hangs |
| Gamma | partially unevaluated integral |
| GammaInverse | evaluation hangs |
| Kumaraswamy | evaluation hangs |
| Laplace | unevaluated integral, including after evaluation |
| Logistic | exception |
| Nakagami | evaluation hangs |
| StudentT | evaluation hangs |
| UniformSum | evaluation hangs |

The independent required set is exactly **one F2P (`test_arcsin`) plus nineteen
P2P tests**. The original hidden test patch touches more distribution tests and
precomputed-CDF checks, but those additional checks are not part of the required
pass definition. The reused reference gate itself has non-scoring errors.
Therefore a whole-task pass is accurately described as “passed the frozen
independent required set,” not “verified all twelve requested distributions.”

The old solo and redesigned episodes both passed the required set while the
non-scoring `test_precomputed_cdf` and `test_long_precomputed_cdf` reported
exceptions. This is existing evidence of limited coverage, not evidence that
every requested CDF is wrong or missing. A broad test can fail before it reaches
an individual distribution. A non-scoring failure shared by the reference gate
must not be counted as a candidate omission without further evidence.

For each new repeat and distribution, inspect the relevant candidate change,
an executed original public example, and (where available) a focused independent
test or numeric/symbolic check. Keep the observed command, result and file path.
An inherited implementation can satisfy a requirement without a new method;
conversely, the existence of a new `_cdf` method does not prove correctness.

## Manual label and contribution rules

- `verified`: concrete behavioral evidence supports this requirement, with
  the exact scope stated; this is not a claim of exhaustive correctness.
- `omitted`: the final candidate demonstrably leaves a requested area
  unimplemented, based on code and behavior rather than a failed test alone.
- `implemented_but_wrong`: a relevant implementation exists but a scoped
  behavioral check demonstrates incorrect behavior.
- `unknown`: missing, ambiguous or environment-confounded evidence. Merely
  reading a file, writing a method or passing an unrelated regression remains
  unknown.

Store manual labels/evidence in a separate audit artifact, not the generated
`omission-diagnostics.json`, which the frozen summarizer may regenerate.
Report coverage denominators and unknown counts. Never silently exclude unknown
requirements or turn a missing trace into a successful contribution.

Worker attribution is a separate question from requirement satisfaction.
Identify the actual child assignment, the child edit or concrete finding, the
report type and completion state, a later parent request containing the result,
and the parent action it informed. Child-produced files can contribute directly
without a model-authored summary; a deterministic fallback can carry evidence
without proving the parent used it. Preserve those distinctions. No worker use
means no attribution to delegation, regardless of the episode's timing.

## Public-example diagnostics

After all 24 paid episodes finished, the copied script
[`diagnostics/public_examples.py`](diagnostics/public_examples.py) ran against the
completed candidate workspaces. It made no model requests and did not change the
frozen grader, score or source. The compact, redacted result is
[`public-diagnostics.json`](public-diagnostics.json); interpretation and limits
are in [`PUBLIC-DIAGNOSTICS.md`](PUBLIC-DIAGNOSTICS.md).

The diagnostics use one public printer expression and three numeric points per
CDF distribution. They only establish the stated sample observations. They do
not prove complete symbolic behavior, support boundaries, arbitrary parameters,
or the twelve-distribution task as a whole. Unknown and timeout observations
remain unknown; they are not silently converted into omissions or failures.
