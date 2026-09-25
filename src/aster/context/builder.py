"""Public context builder: calculate first, install successful compaction once."""
from __future__ import annotations

from .budget import BudgetCalculator


class ContextBuilder:
    def __init__(self, budget: int, output_reserve: int):
        self.budget, self.output_reserve = budget, output_reserve
        self.calculator = BudgetCalculator()

    def count(self, value) -> int:
        return self.calculator.count(value)

    def build(
        self,
        state,
        user: str,
        memory: str = "",
        skills: str = "",
        structured_history: list[dict] | None = None,
        working_memory: str = "",
        *,
        tools: list[dict] | None = None,
        protocol: str = "responses",
        model: str = "",
        skill_bodies: dict[str, str] | None = None,
        memory_enabled: bool = False,
        skills_available: bool = False,
        workers_available: bool = False,
        environment: str = "",
        archive=None,
        validate_archive=None,
        redact_history=None,
    ):
        result = self.calculator.calculate(
            state, user, memory, skills, structured_history, working_memory,
            budget=self.budget, output_reserve=self.output_reserve,
            tools=tools, protocol=protocol, model=model, skill_bodies=skill_bodies,
            memory_enabled=memory_enabled, skills_available=skills_available,
            workers_available=workers_available,
            environment=environment, archive=archive, validate_archive=validate_archive,
            redact_history=redact_history,
        )
        # Preserve the public build mutation boundary; failed calculation installs nothing.
        if structured_history is not None:
            structured_history[:] = result.history
        state.messages[:] = result.messages
        return result.prompt, result.report
