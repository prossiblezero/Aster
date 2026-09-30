# Offline supplementary diagnostic calibration

Completed on 2026-09-30 before the new paid experiment. No model calls were
made. Python processes loaded the pinned candidate SymPy source in the
existing offline Python 3.9 environment; controller checks used Conda `aster`.
The unchanged upstream starter/reference gates and old experiment workspaces
were read, and a fresh CDF reference was reconstructed only in the new private
diagnostic directory.

| calibration | observed result |
| --- | --- |
| Printer original starter, public expression | 0 verified / 3 wrong / 0 unknown |
| Printer upstream reference, public expression | 3 verified / 0 wrong / 0 unknown |
| Printer file-qualified upstream MatrixSymbol tests | three distinct failed starter tests; three distinct passed reference tests |
| CDF starter, Arcsin and Erlang three interior points each | 0 verified / 6 wrong / 0 unknown |
| CDF upstream reference, all 36 interior points | 36 verified / 0 wrong / 0 unknown |
| CDF upstream reference, all 36 boundary points | 33 verified / 2 wrong / 1 unknown |
| Old r3 Solo Arcsin, `x=-1` | wrong: returns `1`, expected `0` |
| Old r1 Solo StudentT, `x=-2` | wrong: returns `0.9633059826146299`, expected approximately `0.03669401738537` |
| Independent math reference cross-check | all 36 interior values agree with installed mpmath 1.3.0, evaluated at 80 digits, within `1e-10` relative / `1e-12` absolute |

The starter Arcsin still returns `Integral` at all three points; starter Erlang
still returns `Float` atoms despite exact parameters. Thus the diagnostics
distinguish a missing analytic path, a precision defect, wrong signs and an
incorrect support branch without assuming that all observed failures are
implementation omissions. The compact assert-based check also confirms that
unsupported rendering, exceptions and timeout evidence stay unknown and that
same-named tests in separate files remain separate IDs.

The upstream CDF reference has concrete limitations under the extra boundary
scope. At Frechet's support start `x=2`, it returns `exp(zoo)` and float
conversion raises `TypeError: can't convert complex to float`; the executable
rule records **unknown** and preserves this observation. StudentT directly
substituted at both infinities yields a nonfinite numerical result, so the
endpoint API diagnostic records two **wrong** results. These do not invalidate
the reference's 36 finite interior sample passes. Expected CDF boundary values
remain unchanged; the reference is not declared all-green. UniformSum's finite
`Sum` evaluates correctly at every selected point and is accepted.

Relevant original sources were read before implementation:

- SymPy public issues [14248](https://github.com/sympy/sympy/issues/14248) and
  [13878](https://github.com/sympy/sympy/issues/13878), preserved byte-for-byte in
  the two pinned Aster source datasets.
- The issue-14248 upstream test patch adds separate MatrixSymbol tests in
  `sympy/printing/tests/test_str.py`, `sympy/printing/pretty/tests/test_pretty.py`
  and `sympy/printing/tests/test_latex.py`; original full test logs retain their
  file headers. These were inspected directly, without the short-name parser.
- The issue-13878 source is based on commit
  `7b127bdf71a36d85216315f80c1b54d22b060818`; the pinned upstream reference patch
  is `ed22192763dcd7277e313588c9792eaf52a7513574be95592fe597c26e8214c3`.
  Its support guards and StudentT hypergeometric formula were inspected.
- The mathematical oracle is independent controller `math` code, cross-checked
  with installed mpmath beta/incomplete-gamma and elementary formulas. It does
  not copy values out of the candidate or assume the upstream patch is correct.

Private detailed outputs are under
`artifacts/private/worker-owned-20260930/diagnostic-preflight/`:
`printing-starter.json`, `printing-reference.json`, `cdf-starter.json`,
`cdf-reference.json`, `known-Arcsin.json`, `known-StudentT.json`,
`math-reference.json`, and the hashed index `preflight.json`.
`cdf-starter.json` explicitly marks its six-point calibration subset; it is
not a full starter coverage result. The full reference CDF check took about
160.4 seconds; the two printer checks took about 1.6 seconds each. These times
are calibration overhead, not agent task latency measurements.

The forthcoming 60-episode paid experiment must use the frozen supplemental
script and case list unchanged. Supplementary checks run only after all paid
episodes and original grading finish; their results remain separate columns.
