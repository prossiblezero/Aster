# Repository benchmark execution environment

The seven repository tasks retain their frozen prompts, source revisions, hidden
tests and F2P/P2P scoring. This adapter runs their original tests in independent
native Linux copies protected by Aster's Landlock/seccomp launcher. It does not
claim an official Docker/Harbor benchmark score. Gold patches and hidden tests
exist only in the controller and independent grader workspace.

Preparation uses Conda clones of `/home/dzh/.conda/envs/aster`; base and the source
aster environment are unchanged. Aster's driver remains on Python 3.12, importing
`/home/dzh/Aster/src`. Each task's public testing interpreter is selected by
`prepare()` metadata and is identical for the solo and Worker arms.

## Recreate the materials

Run `python datasets/worker-code-v2/checks/verify.py --fetch --check-cache` from
Conda aster. The frozen `sources.json` supplies every base source archive and
benchmark record, with SHA-256 verification. The content-addressed cache is
`artifacts/private/worker-code-v2/objects` and must never be agent-readable.

For Verified, use pyarrow in a controller-only temporary environment to read
object `030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`.
Select exactly the five Verified `upstream_id` values in the frozen instances
file; save original rows, keyed by instance ID, as
`artifacts/private/worker-code-20260928/repository_rows.json`. Do not drop columns.
The adapter verifies each original patch, test patch and eval-script checksum
against frozen task metadata on every use.

Download the Verified parser from:

```
https://raw.githubusercontent.com/SWE-bench/SWE-bench/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/log_parsers/python.py
SHA256 cd56156414f8327221e525665ace9b184f7d73e83b272d9eb3f545fb17c2d9bc
```

Save it as `artifacts/private/worker-code-20260928/official_log_parsers.py`.
Its checksum is enforced before extracting unchanged pure parser functions.
Pro parsers, run scripts, gold/test patches and config are loaded directly from
the verified frozen objects; Pro requires no generated private row file.

## Verified Python environment

Clone aster to `/tmp/aster-worker-repository-py39-20260928`; use Conda there to
install `python=3.9 pip<25 setuptools<76 wheel<0.46 packaging<25` (the recorded
run resolved Python 3.9.25). Install these preparation dependencies there:

```
pytest==8.3.3 mpmath==1.3.0 asgiref==3.5.2 sqlparse==0.4.4 pytz==2024.2
Jinja2==2.11.3 MarkupSafe==2.0.1 docutils==0.17.1 Pygments==2.18.0
Babel==2.16.0 alabaster==0.7.11 imagesize==1.4.1 snowballstemmer==2.2.0
requests==2.32.3 sphinxcontrib-applehelp==1.0.4 sphinxcontrib-devhelp==1.0.2
sphinxcontrib-htmlhelp==2.0.1 sphinxcontrib-jsmath==1.0.1
sphinxcontrib-qthelp==1.0.3 sphinxcontrib-serializinghtml==1.1.5
html5lib==1.1 cython==3.0.11
```

`repository-py39-pip-freeze.txt` in the private run artifacts records the full
resolved dependency versions. The tested Django, Sphinx and SymPy packages are
loaded from each current source copy via PYTHONPATH, never a stale editable
installation. The source package itself is not installed in this shared env.

Sphinx uses the same pytest selection directly instead of tox orchestration.
Django parsing preserves upstream docstring IDs as well as Python 3.9's multiline
test IDs. Mail's grader requires sockets for original local SMTP tests: only
that independent grader enables networking, and the sandbox does not restrict
socket destinations. Model execution remains offline. SymPy's authoritative
435 F2P/P2P entries determine success; five baseline exceptions outside that
list remain visible in raw logs, exit codes and `non_scoring_failures`. A passing
score does not claim the whole selected SymPy suite has no errors.

## OpenLibrary Pro environment

Clone aster to `/tmp/aster-openlibrary-py311-20260928` and switch the clone to
Python 3.11. Install the frozen OpenLibrary base's `requirements.txt`, replacing
only `psycopg2==2.9.6` with `psycopg2-binary==2.9.6`, plus
`pytest==7.4.4 pytest-asyncio==0.23.6`. This avoids system PostgreSQL headers.
Python 3.11 supplies wheels for these original pinned dependencies; it differs
from the project's pyproject declaration of 3.12.2. Full resolved versions are
in the private `openlibrary-py311-pip-freeze.txt`.

The base Git tree points `vendor/infogami` to commit
`3a6508785487728911252a9df53bcc5f44a4cde1`. Download:

```
https://codeload.github.com/internetarchive/infogami/tar.gz/3a6508785487728911252a9df53bcc5f44a4cde1
SHA256 708fb97c5f5a50db197a8b8885fb767781d3a6e77f319a3bdeae9f084558b3d9
```

Save as private `openlibrary-infogami.tar.gz` with
`openlibrary-infogami-source.json` containing those `url`, `sha256`, `commit`
and `bytes: 216749` fields. `prepare()` restores this public submodule and
materializes its internal `infogami` symlink because Aster omits symlink paths.
No hidden material is involved. The original run script is retained, with only
its absolute `PYTHONPATH=/app` relocated to the independent source directory.
The selected catalog tests do not need a browser. The upstream script starts
Xvfb in the background without checking its result; absence of Xvfb therefore
does not skip or modify any of the 28 selected test assertions.

## Flipt Pro environment

Clone aster to `/tmp/aster-flipt-go-20260928`. Extract this verified Go archive
there, producing `go/bin/go`:

```
https://go.dev/dl/go1.20.14.linux-amd64.tar.gz
SHA256 ff445e48af27f93f66bd949ae060d97991c83e11289009d311f25426258f9c44
```

With the frozen Flipt source as cwd, prepare dependencies using:

```
GOPATH=/tmp/aster-flipt-go-20260928/gopath \
  /tmp/aster-flipt-go-20260928/go/bin/go mod download
```

Network is allowed only in this trusted dependency preparation. At model and
grader runtime, `GOPROXY=off`, `GOSUMDB=off`, GOROOT and GOMODCACHE point at that
read-only environment, and GOCACHE is an execution-workspace `.tmp/go-build`.
The cached modules occupy roughly 3 GB unpacked. Source go.mod/go.sum remain
unchanged. The original verifier defaults to SQLite; it does not need Docker or
external SQL services for this selection.

The normal Aster sandbox's 16 MiB file limit prevents sqlite3 C compilation.
This task's evaluation-only launcher therefore uses a 512 MiB file limit and
300-second per-process CPU limit, for both agent arms and the grader. Parent
wall-clock, token and tool budgets remain unchanged. No production source is
modified. These resource overrides are explicit `prepare()` metadata.

## Replay and scoring

Call `repository_adapter.preflight(task, cache, output)` to reuse a saved
completed gate or run starter and reference in independent copies. `prepare`
returns public source and test-environment metadata only. `grade` restores
original hidden test files before applying the frozen test patch; Pro also
restores upstream test-code/fixture surfaces, preserving the exact official
`openlibrary/mocks/mock_infobase.py` exception.

Scoring requires all frozen F2P and P2P names to pass. Missing tests, timeouts,
sandbox startup failures and reference failures block readiness. Process exit
and extra failing tests are retained; upstream F2P/P2P semantics, rather than an
unrelated full-suite exit, determine the task result. Pro's Go parser only emits
passing names; failed top-level test observations are additionally recorded
from its native log without changing the required-pass decision.

The private `repository-environment-provenance.json` records upstream environment
URLs/hashes and native adaptations. Keep that file, dependency freezes, cached
objects and raw starter/reference logs with any published evaluation result.

## Public test infrastructure integrity correction

Before any repository-task model episode started, an offline probe found that
modifying Django's public `tests/runtests.py` could report passing unittest
results without running test methods. Restoring only files named in the hidden
test patch was insufficient. No paid model was used in this probe.

The corrected grader also restores the following public infrastructure from
the frozen source before applying hidden tests:

- Django: the complete `tests/` tree, including its runner, settings and fixtures.
- Sphinx: `tests/` and its `sphinx/testing/` fixture/plugin helpers.
- SymPy: nested `tests/` trees, `bin/test`, `bin/get_sympy.py`,
  `sympy/utilities/runtests.py`, `sympy/utilities/pytest.py` and conftest files.
- Verified test configuration: conftest, pytest.ini, tox.ini, setup.cfg and Python
  startup customization files. Other product implementation changes remain.

`discarded_test_changes` records candidate changes excluded from independent
scoring. The original Pro restoration and its mock exception are preserved.
The same runner-tampering probe now fails its original F2P test, and all five
Verified starter/reference gates were replayed under this corrected policy.
Previous reports and vulnerable/fixed probe evidence are retained beneath
private `audit/repository-grader-v1/` and `audit/repository-grader-v2/`.
This protects the benchmark's public test infrastructure; it does not claim to
contain arbitrary adversarial Python that modifies test behavior from legitimate
product implementation modules. Model edits and native logs remain auditable.
