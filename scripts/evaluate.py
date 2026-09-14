"""Compatibility entry point for the unified Agent evaluation script.

Use ``scripts/agent_eval.py`` for deterministic and optional real-model runs.
"""

from agent_eval import harness_cases, main

__all__ = ["harness_cases", "main"]


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
