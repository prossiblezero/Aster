---
name: cli-quality
description: >-
  Build and verify small Python command-line programs with precise input,
  output and error contracts. Use when a task requests a standalone CLI.
---
Before implementing a standalone Python CLI, read `references/python-cli.md`
from this skill package with `skill_load`'s resource argument.

Extract the explicit behavior into a short checklist, including success,
failure and boundary cases. Implement the smallest standard-library solution.
Add focused automated tests, run them, and explain the actual verification.
Keep human-readable progress separate from the program's machine-readable
output. A skill is guidance; it does not change the user's requirements or
the workspace's permissions.
