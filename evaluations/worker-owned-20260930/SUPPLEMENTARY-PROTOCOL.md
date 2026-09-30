# Frozen supplementary diagnostics, 2026-09-30

These diagnostics are a separate outcome column for the fixed three-arm,
two-repeat experiment. They do not change the original benchmark score,
F2P/P2P requirements, candidate prompts, task selection or model budgets.
The two tasks are already seen development tasks, not unseen holdouts:
`swe-verified-sympy__sympy-14248` and `holdout-sympy__sympy-13878`.
All six candidates for each task receive the same checks, including candidates
that stopped or failed the original benchmark. No best-run selection is used.

`diagnostics/public_examples.py` extends the prior public-example diagnostic.
The controller uses Conda `aster`; candidate imports use the already prepared
`/tmp/aster-worker-repository-py39-20260928/bin/python`. Each child verifies
that imported SymPy resolves inside the requested candidate workspace. Its
workspace is read-only under the existing Landlock/seccomp launcher, network
access is disabled, bytecode writes are disabled, and stdout/stderr are stored
outside the candidate. No new dependency or product test suite is added.

Each printer or CDF point has a separate process with a **20-second wall limit**.
A timeout kills the whole process group. One slow point cannot prevent later
points or support-boundary checks. A full CDF candidate has 72 processes
(36 interior and 36 boundary points), giving a hard maximum of 1,440 seconds
plus process cleanup overhead; normal points are much faster. Diagnostic
time and calls are never added to the paid model's efficiency metrics.

## Printer checks

The original issue asks that `A - A*B - B` print as signed subtraction in
`str`, Unicode pretty and LaTeX, rather than `(-1)*B`, `-1 B` or `+ -` terms.
The diagnostic uses the original `2 × 2` MatrixSymbols and accepts all term
orders with the coefficients `A:+1`, `A*B:-1`, `B:-1`. Whitespace, Unicode
middle dots and LaTeX `\cdot` do not affect this match.

- `verified`: standard signed terms match the three coefficients exactly.
- `wrong`: a recognized simple rendering has a wrong/missing/extra signed
  term, an explicit unit coefficient, or a plus-negative sequence. Algebraic
  equivalence does not make the explicit unit-factor formatting requested in
  this issue satisfactory.
- `unknown`: unsupported rendering syntax, import/process failure, exception,
  timeout, or missing observation. The script is deliberately not a general
  LaTeX/algebra parser. Unknown is not an automatic failure.

Separately, the diagnostic reads the **unaltered independent grading log**.
Every native `sympy/...py[N]` header scopes subsequent test rows, producing
`file.py::test_name` IDs and preserving every observed status. In particular,
these three tests are distinct:

| printer | upstream diagnostic test |
| --- | --- |
| str | `sympy/printing/tests/test_str.py::test_MatrixSymbol_printing` |
| pretty | `sympy/printing/pretty/tests/test_pretty.py::test_MatrixSymbol_printing` |
| LaTeX | `sympy/printing/tests/test_latex.py::test_MatrixSymbol_printing` |

The upstream tests use exact output strings and some different expressions;
they remain separate from the public-expression standard-form result. All
other file-qualified native results, including same-named MatrixElement
tests, are preserved in the private JSON. Conflicting duplicate statuses or
missing rows are unknown. No short-name test mapping overwrites another file.
No tests are rerun or patched by this log-reading step.

## CDF checks

Constructors and first interior points are verbatim from the public issue.
The next two interior points are the fixed prior diagnostics. Finite support
endpoints and adjacent outside points extend the coverage before any new
model runs. `-oo`/`oo` check direct public API substitution, not symbolic limits.

| distribution | constructor arguments after name | interior points | boundary points (expected CDF) |
| --- | --- | --- | --- |
| Arcsin | `0, 3` | `1, 1/2, 2` | `-1→0, 0→0, 3→1, 4→1` |
| Dagum | `1/3, 1/5, 2` | `3, 1, 4` | `-1→0, 0→0, oo→1` |
| Erlang | `1, 1` | `1, 1/2, 2` | `-1→0, 0→0, oo→1` |
| Frechet | `4/3, 1, 2` | `3, 5/2, 4` | `1→0, 2→0, oo→1` |
| Gamma | `0.1, 2` | `3, 1, 5` | `-1→0, 0→0, oo→1` |
| GammaInverse | `5/7, 2` | `3, 1, 5` | `-1→0, 0→0, oo→1` |
| Kumaraswamy | `1/123, 5` | `1/3, 1/10**123, 1/2**123` | `-1→0, 0→0, 1→1, 2→1` |
| Laplace | `2, 3` | `5, -1, 2` | `-oo→0, oo→1` |
| Logistic | `1, 0.1` | `2, 0, 1` | `-oo→0, oo→1` |
| Nakagami | `7/3, 1` | `2, 1/2, 1` | `-1→0, 0→0, oo→1` |
| StudentT | `10` | `2, -2, 0` | `-oo→0, oo→1` |
| UniformSum | `5` | `2, 1, 4` | `-1→0, 0→0, 5→1, 6→1` |

All non-decimal fractions above use SymPy `S` exactly; the two `0.1` parameters
remain Python floats as in the issue. Each point must return no unevaluated
`Integral`, a finite probability in `[0, 1]`, and agree with an independent
Python `math` reference (`rel_tol=1e-8`, `abs_tol=1e-10`). Erlang also must
contain no SymPy `Float` atoms for exact inputs. A finite `Sum` that evaluates
correctly is allowed: the original request explicitly permits a sum formula.

Math references use elementary distribution formulas, a convergent lower
incomplete-gamma series, Simpson integration of the StudentT density, and
the Irwin–Hall finite sum. The 36 interior expected values are independently
cross-checked before paid runs against **installed mpmath 1.3.0 at 80 digits**,
including beta-function forms for Arcsin/StudentT and incomplete-gamma
functions. This process imports no candidate SymPy. Boundary values follow
the stated supports and the definition of a continuous CDF.

A concrete numeric mismatch, out-of-range/nonfinite result, unevaluated
Integral, or Erlang float atom is `wrong`. A timeout, absent output,
process/import failure or conversion exception is `unknown`, with raw evidence
retained. Known violated checks take precedence over a later conversion
exception (e.g. a returned Integral is still observed unresolved). No result
alone is labeled `omitted`; omission requires a separate source/trace audit.

Report per-point counts and per-distribution **interior** and **boundary**
results separately. A group is verified only when every point is verified;
it is wrong if any point is wrong, otherwise unknown. The combined group uses
the same rule but preserves all constituent wrong and unknown counts. These
fixed sample checks do not prove arbitrary-parameter or symbolic correctness.
Direct infinite endpoint failures are additional API diagnostics; they must
not be described as failures of the issue's finite examples.

## Non-model calibration and freeze

`diagnostics/preflight.py` reads original public issues, unchanged test logs,
prepared printer starter/reference workspaces, and the pinned CDF base and
upstream reference patch. It reconstructs the CDF reference only under the
new private diagnostic directory. Prior experiments and product source remain
untouched. It checks real starter failures, reference behavior, and two known
prior defects: r3 Solo Arcsin below support and r1 Solo StudentT negative sign.
One small assert-based classifier check also covers unknown/timeout handling
and two same-named tests in different files. It adds no pytest cases.

Reference patches are **calibration evidence, not the mathematical oracle**.
They are not required to pass every stronger boundary or default-API check.
Observed reference limitations and the full preflight results are recorded in
`diagnostics/PREFLIGHT.md` and the private `diagnostic-preflight/*.json` files.
No expectations are weakened merely to turn a reference result green.

Before paid runs, `diagnostics/batch.py freeze` writes `diagnostics/freeze.json`
with script/protocol/selection/interpreter/launcher/preflight artifact hashes,
all fixed cases and the point timeout. `audit` rejects any changed input.
The root paid driver binds this pre-existing freeze digest. The batch runner
then requires all **60** episode `result.json` files, including stopped and
failed results, and holds the same `controller.lock` as the paid controller.
Thus supplementary execution happens after paid work and outside the original
grading operation, with no resource overlap. Existing diagnostic files are
reused only when their frozen identity and episode result hash match.

```bash
/home/dzh/.conda/envs/aster/bin/python -B evaluations/worker-owned-20260930/diagnostics/preflight.py
/home/dzh/.conda/envs/aster/bin/python -B evaluations/worker-owned-20260930/diagnostics/batch.py freeze
/home/dzh/.conda/envs/aster/bin/python -B evaluations/worker-owned-20260930/diagnostics/batch.py audit
# Only after all 60 paid episodes and their independent grading have finished:
/home/dzh/.conda/envs/aster/bin/python -B evaluations/worker-owned-20260930/diagnostics/batch.py run
```

Raw diagnostic artifacts and aggregate separate-column results are written to
`artifacts/private/worker-owned-20260930/diagnostics/`. Public reporting keeps
verified, wrong and unknown denominators visible for every arm and repeat.
