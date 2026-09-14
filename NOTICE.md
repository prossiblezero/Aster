# Design references

Aster is an independent Python implementation. It does not import or vendor the
runtime of another agent. Capability requirements and design lessons were studied from:

- Local `pico-v3` and `nexa` projects supplied by the user: execution, context,
  memory, recovery and evidence requirements. Attribution is retained here.
- [OpenAI Codex](https://github.com/openai/codex), Apache-2.0: approval/sandbox
  separation, protected metadata, structured events and policy tests.
- [Claude Code documentation](https://code.claude.com/docs): lifecycle hooks,
  incremental memory and untrusted observations. Historical community-restored
  source was consulted for design discussion; no implementation is vendored.
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code): explanatory
  progression from concrete problems to runnable mechanisms.

References are design context, not claims of implementation or performance parity.

Benchmark fixtures and problem data have separate provenance documented in
[datasets/SOURCES.md](datasets/SOURCES.md). HumanEval problem data and reference
solutions are used under the included MIT license; source attribution is retained.
