"""Ports used by Mission application use cases."""

from __future__ import annotations

from typing import Callable, ContextManager, Protocol


class MissionInitializer(Protocol):
    """Persistence boundary for the legacy v4 bootstrap transaction."""

    def initialize(self, arguments: object) -> None:
        ...


class MissionRepository(Protocol):
    """Behavior shared by the v4 compatibility repository and future v5 UoW."""

    def transaction(self) -> ContextManager[object]:
        ...

    def load(self) -> dict:
        ...

    def execute(self, state: dict, mutation: Callable[[dict], None], transition=None) -> dict:
        ...

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
        ...


class MissionClock(Protocol):
    def now(self) -> str:
        ...


class MissionIdentity(Protocol):
    def session_id(self) -> str:
        ...
