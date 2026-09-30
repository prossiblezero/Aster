# Explicit subagent investigation and review scenarios

Six Aster-authored tasks derived from three fixed public code snapshots already archived for the mixed-source Worker benchmark. They assess explicit independent reading and change review, not original issue repair or official leaderboard performance. Three investigation tasks and three review tasks reuse the respective source snapshot; each task has two work packages and eight behavior questions.

- Django at `76e37513e22f4d9a01c7f15eee36fe44388e6670`, obtained through the existing SWE-bench Verified task snapshot: multivalue containers and regex reversal.
- OpenLibrary at `0d13e6b4bf80bced6c0946b969b9a1b6963f6bce`, obtained through the existing SWE-bench Pro snapshot: list seed aggregation and independent compression contexts.
- Terminal-Bench `session-window-debug` source from the fixed asset records in `worker-code-v2`: watermark/session assignment and emission/retention. See upstream URLs and hashes in manifest.json.

`instances.json` holds all tasks, selected source asset references, exact review edits, question snippets and balanced three-arm schedules. No task is embedded in the runner. `oracle.json` is evaluator-only provenance for source locations; actual expected behavior is computed offline by executing the source, not guessed from keywords. Neither metadata file is copied into the model workspace. The runner materializes only selected source files and a public request; original versions of changed files are additionally supplied for review. Investigation asks for actual behavior, including existing defects. Review introduces four regressions per task and includes four unchanged behavior controls.

The code is not duplicated in Git. Source restoration uses `artifacts/private/worker-code-v2/objects` and validates archive/file SHA-256. Dataset lineage and upstream licenses remain in `datasets/worker-code-v2/sources.json`; Django, OpenLibrary, and Terminal-Bench retain their upstream licenses. Our derived questions and patches do not replace those licenses. This is a small development scenario experiment with known upstream snapshots, not new held-out benchmark evidence.

Run the offline gate with the Conda aster interpreter: `python evaluations/subagent-light-20260930/evaluate.py preflight`. The evaluation protocol defines scoring, budget and limitations.
