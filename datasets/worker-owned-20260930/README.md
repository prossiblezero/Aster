# Owned Worker development selection

`selection.json` freezes ten existing coding tasks by source dataset, record and
prompt hashes. The examples stay in their original versioned datasets; the runner
loads them by ID rather than embedding tasks in Python. Difficulty is Aster's
existing local stratum, with 3 simple / 5 medium / 2 hard and five sources.

All tasks were previously evaluated. This selection is development regression,
including three simple overhead controls and the coupled Forth control. It is not
a newly unseen holdout. Two fresh pass@1 repeats for each of solo/current/worker
produce 60 episodes; no best-of-two aggregation is used.

Each task includes its two frozen arm orders and a reused starter/reference gate
hash. The original neutral prompt suffix and immutable independent graders remain
unchanged. Current binds `6ccb8be1ef994f490eaa79a3786c0fb9c18df7e4`; solo/worker must
bind one identical newer product checkout. Full policy and commands are in
[优化历程](../../docs/optimization-history.md).
