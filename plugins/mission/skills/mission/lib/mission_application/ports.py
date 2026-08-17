"""Ports used by Mission application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ContextManager, Optional, Protocol, overload

from mission_kernel.model import FrozenJsonObject, MissionState
from mission_kernel.transitions import Decision


class AggregateIndexError(RuntimeError):
    """The authoritative write succeeded but its rebuildable index did not."""


@dataclass(frozen=True)
class AuditMetadata:
    command_type: str
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionRequest:
    """All authority-bearing input for one repository execution."""

    session_id: str
    lease_owner_session_id: str
    command: FrozenJsonObject
    blobs: object
    operation_id: str
    intent_digest: str
    presented_lease_id: Optional[str]
    audit: AuditMetadata


@dataclass(frozen=True)
class CommitResult:
    commit_digest: str
    generation: int
    head_digest: str
    state_generation_digest: str


@dataclass(frozen=True)
class RepositoryExecutionResult:
    accepted: bool
    commit: Optional[CommitResult]
    rejection_code: Optional[str]


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

    @overload
    def execute(
        self,
        request: ExecutionRequest,
        mutation: Callable[[MissionState], Decision],
        transition=None,
    ) -> RepositoryExecutionResult:
        ...

    @overload
    def execute(self, state: dict, mutation: Callable[[dict], None], transition=None) -> dict:
        ...

    def execute(self, state, mutation, transition=None):
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


class RecoverableUnitOfWork(MissionRepository, Protocol):
    """Stronger v5 repository protocol; legacy v4 must not claim this type."""

    def begin(self, request: ExecutionRequest) -> object:
        ...

    def stage(self, admitted: object, transition: object, blobs: object) -> object:
        ...

    def commit(self, prepared: object, precondition: object) -> CommitResult:
        ...

    def recover(self, session_id: str) -> object:
        ...

    def collect(self, policy: object = ...) -> object:
        ...


class MissionClock(Protocol):
    def now(self) -> str:
        ...


class MissionIdentity(Protocol):
    def session_id(self) -> str:
        ...
