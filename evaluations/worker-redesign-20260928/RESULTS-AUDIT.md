# Worker redesign evaluation audit

Generated from the frozen result directories on 2026-09-29 with:

```text
PYTHONPATH=src /home/dzh/.conda/envs/aster/bin/python \
  evaluations/worker-redesign-20260928/summarize.py
```

The summarizer completed without an input mismatch and reported **26/26
complete triples and 78/78 episodes**.  All 26 preflights were ready and no
incomplete episode directory remained.  The three-arm checks passed for every
task: prompt, endpoint hash, limits, preparation, Python, driver hashes,
comparable settings, and initial product workspace hashes matched within each
triple.

## Aggregate results

| arm | passed | runtime completed | total tokens | seconds | workers started/completed | persisted handoffs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| solo | 17/26 | 14 | 10,303,109 | 7,188.4 | 0/0 | 0 |
| handoff_only | 15/26 | 16 | 12,513,269 | 7,667.1 | 23/7 | 23 |
| redesigned | 15/26 | 17 | 11,204,563 | 8,302.6 | 21/8 | 21 |

The result is a descriptive three-arm evaluation, not a causal claim that
every worker or handoff helped.  The redesigned arm had 13 partial handoffs,
4 automatically delivered handoffs, 37 coordination calls, 81 shared
parent/child successful read paths, and 2,461,219 child tokens.  The frozen
summarizer does not yet distinguish model-authored reports from deterministic
fallbacks or prove that a parent consumed each persisted handoff; those are
trajectory-audit metrics and must not be inferred from the aggregate counts.

By split, the pass counts were:

| split | solo | handoff_only | redesigned |
| --- | ---: | ---: | ---: |
| regression (20) | 13 | 13 | 12 |
| holdout (6) | 4 | 2 | 3 |

The clearest complete task with an end-to-end latency improvement for a
working delegated run was `terminal-cobol-modernization`: redesigned passed
in 199.6 s with 323,893 tokens and one completed worker, versus 268.6 s and
370,926 tokens for `handoff_only`.  It still used more tokens than solo, so it
should be reported as a conditional latency/coordination result rather than a
general cost reduction.

## Accounting and source checks

- `usage_matches`: **78/78 true** (request ledger totals equal result totals).
- `http_usage_matches`: **78/78 true** after ledger reconciliation.
- HTTP attempts: **1,686**; attempts with numeric provider usage: **1,673**.
  The remaining **13 attempts** had no numeric usage (transport/incomplete
  response or cancellation boundary), so exact billed cost cannot be derived
  from this run.  They are listed in `results.json` by `http_attempts` and
  `http_with_total_usage`.
- `source_unchanged`: **78/78 true**.
- Solo and handoff-only source commit: `e1b68c9ba755648e917ef03bd651a625e27fdcd6`.
- Redesigned source commit: `8c2afa29587fd97fc75946a53d50b0e807d3ef6a`.
- All 78 episodes used `gpt-5.6-luna`; nine incomplete/transport attempts have
  an empty response-model field and no numeric usage.

The first paid solo grade-school episode overlapped the local final pytest
and wheel validation for approximately 31.6 seconds.  That latency confound
was recorded in `host-load-events.jsonl`; the episode was preserved and not
rerun.

## Grading and failures

All 26 preflight gates passed their starter/reference integrity checks.  The
public result set contains 30 grader runs with status `success`, 17 with
status `passed`, 30 with status `failed`, and one `environment_error`.  The
`failed` statuses are task outcomes or incomplete agent runs, not evidence that
the immutable grader was changed.

The sole grader environment error was
`swe-pro-flipt-segments--handoff_only`: all three required tests
(`TestDBTestSuite`, `TestExport`, `TestImport`) were missing from the observed
run because the agent stopped before producing a grade.  No other episode had
missing grader tests.  The immutable grader reported target test failures for
the remaining failed tasks; those failures and their raw logs remain under
each episode's `grading/` directory.  Provider stops include budget limits,
incomplete responses, one cancellation, and one stall-recovery exhaustion;
these are retained in each `result.json` and are not silently converted into
task failures or successes.

The generated machine-readable artifacts are `summary.json`, `results.json`,
`pairs.json`, `coverage.json`, and `preflight-summary.json` in this directory.
