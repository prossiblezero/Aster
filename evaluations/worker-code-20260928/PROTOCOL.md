# Worker Code v2: frozen paired evaluation, 2026-09-28

Dataset: `datasets/worker-code-v2`, commit `0b8d867`. Product implementation remains at that commit. The 20 task IDs, prompts, difficulty labels and sources are unchanged. Evaluation adapters read all task content and tests from dataset references, not embedded benchmark questions.

Two arms: `solo` (`max_workers=0`) and `current_worker` (`max_workers=3`). Both use the configured `.env` model and endpoint, Responses streaming, 64k context/8192 output, strict completion, no long-term memory, no network, and the same task prompt plus public environment instructions. The neutral common suffix says delegation is available only when useful; it specifies no count, partition, answer paths or worker mode. No forced-delegation arm or redesign is tested. Existing Worker limits (12 steps/12 turns per child, no grandchildren) stay unchanged.

One attempt per arm per task. Alternating arm order by frozen dataset index; episodes run sequentially to avoid distorting wall time by parallel local executions. Model retry logic remains unchanged and all transport attempts are counted. Runs are never silently retried or discarded. The driver resumes only absent episode result files; incomplete invocations are retained and explicitly classified before any further action.

Whole-team ceilings (all parent/child model calls and tool admissions):

| Stratum | Model calls | Tools | Reported tokens | Wall seconds |
|---|---:|---:|---:|---:|
| simple | 40 | 60 | 500000 | 600 |
| medium | 60 | 90 | 750000 | 900 |
| hard | 80 | 120 | 1000000 | 1200 |

Token ceiling stops new requests after the reported total reaches the cap; concurrent in-flight requests are not precisely reserved. It is a reported-usage stopping rule, not a hard billed-token guarantee. Request ledger and persisted run-tree totals are reconciled. Missing usage is reported as incomplete, never silently treated as a complete zero cost. No confirmed relay pricing is available; report input/output/cache tokens and relative total tokens rather than invent dollar charges.

Before each eligible task, require initial failure and reference success under the SAME independent scorer used for both arms. Candidate files are copied to a scoring workspace and grader material is added only after the episode. Original Aider tests remain public; all oracle content and private dataset rows remain outside model filesystem visibility. Grader result is primary task correctness; Runtime completion is reported separately, including code that passes after a timeout or completion-gate failure.

Native container scoring and portable assertion replay are separate result groups. Path/port/interpreter adaptations, unavailable official anti-cheat gates, and incomplete environments must be recorded. A portable scorer can produce an explicitly labelled Aster adaptation result, not an official benchmark score. Tasks unable to pass the preflight gate remain in the denominator of coverage (20), with no model pass/fail assigned. No replacement tasks.

Publish task outcomes, paired wins/losses, whole-tree token/tool/model-call totals, wall time, Worker use/completion, coordination calls and scope conflicts, by source/difficulty/collaboration cohort. Task manifests and actual loaded source/adapter hashes are captured for each launch. Raw workspaces, traces, grader and transport records stay under `artifacts/private/worker-code-20260928`; publish sanitized numeric summaries and this protocol.

Environment preparation record: Aster itself runs from Conda aster Python 3.12. Repository tests use prepared aster-derived Python 3.9/3.11 environments or Go 1.20.14; Terminal gRPC uses an aster-derived Python venv. Interpreters and read-only dependencies are selected by evaluation-only adapters. Flipt needs a 512 MiB per-file and 300-second per-process CPU allowance for sqlite3 compilation; the paired episode budgets above stay unchanged. See `REPOSITORY-ENVIRONMENT.md` and adapter metadata for exact adjustments. Original container resource limits are not reproduced.

Harness implementation was completed while the first control episodes ran. The initial launches recorded hashes but not full driver file snapshots; later launches also preserve content-addressed snapshots. This is a fixed task/model/budget comparison, not a claim that every harness byte was unchanged throughout preparation. The report must disclose paired driver-hash differences. Inputs, product source, initial workspaces and settings other than max_workers are checked for equality before publishing paired results.
