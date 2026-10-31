"""Scenario abstract base class.

Each scenario is a class subclassing `Scenario`. The runner discovers them
via explicit import list (`_registry.py`), sorts by `order`, and calls
`run(ctx)` with the ScenarioContext. Zero central dispatch — adding a new
scenario is one new file, one line in the registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ._context import ScenarioContext


class Scenario(ABC):
    """One operator-facing behavior being verified.

    Subclasses set `order` (int, used for stable run sequence), `name`
    (short identifier used in CLI output and logs), and `description`
    (one-line what this proves).
    """

    order: int
    name: str
    description: str

    @abstractmethod
    def run(self, ctx: ScenarioContext) -> None:
        """Mutate state via `ctx` and assert outcomes. Raise AssertionError
        on failure. The runner handles DB wipe + reporting around this call."""
        raise NotImplementedError
