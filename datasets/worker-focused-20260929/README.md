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

Original assets, licenses, dependencies and independent scorers are reused
unchanged. No content is added to training or to `exam_project`.


历史实验的设计取舍与证据边界见[优化历程](../../docs/optimization-history.md)；逐轮原始结果不随公开仓库分发。
