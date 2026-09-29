# Four-task focused development replication

`selection.json` references four unchanged records from `worker-code-v2` and
`worker-code-v3-holdout`, with dataset/record/prompt and reused preflight hashes.
It contains no copied problems or solutions. All four tasks were evaluated
previously; the former holdout task is now a development task.

| task | stratum | structural reason |
| --- | --- | --- |
| Django-16256 | medium | ordinary/reverse and generic related-manager implementations |
| SymPy-14248 | medium | consistent subtraction rendering across printers |
| OpenLibrary authors | hard | matching priority and backend query semantics |
| SymPy-13878 | hard | multiple requested distribution CDFs |

The list is fixed before this 24-episode run, following earlier observed
outcomes. It excludes simple controls, terminal adaptations, Go and unselected
issues. It is not a representative or unseen benchmark. Three repeats retain
all successes, failures and provider errors.

Selection/omission annotations stay outside model input. Subtest diagnostics
do not replace whole-task correctness; incomplete oracle coverage and missing
manual evidence are explicitly `unknown`. In particular, the inherited SymPy
printer parser collapses duplicate names and the CDF grader scores only Arcsin
as F2P. See the [protocol](../../evaluations/worker-focused-20260929/PROTOCOL.md)
for same-source arm binding, identical internal budgets and reporting rules.

Original assets, licenses, dependencies and independent scorers are reused
unchanged. No content is added to training or to `exam_project`.
