"""Ports used by Mission application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ContextManager, Optional, Protocol, overload, runtime_checkable

from mission_kernel.model import FrozenJsonObject, MissionState
from mission_kernel.commands import Command
from mission_kernel.transitions import Decision


class AggregateIndexError(RuntimeError):
    """The authoritative write succeeded but its rebuildable index did not."""

    def __init__(self, message: str, *, execution: object | None = None) -> None:
        super().__init__(message)
        self.execution = execution


@dataclass(frozen=True)
class AuditMetadata:
    command_type: str
    event_types: tuple[str, ...]


class BlobBindingView(Protocol):
    """Persistence-neutral immutable identity used by an execution request."""

    blob_id: str
    kind: str
    relative_path: str
    digest: str
    size: int


class VerifiedBlobView(Protocol):
    binding: BlobBindingView
    content: bytes


class VerifiedBlobSetView(Protocol):
    blobs: tuple[VerifiedBlobView, ...]


@dataclass(frozen=True)
class ExecutionRequest:
    """All authority-bearing input for one repository execution."""

    session_id: str
    lease_owner_session_id: str
    command: FrozenJsonObject
    # Structural typing keeps the application port independent of a concrete
    # persistence module.  This is a static view only: repository entry still
    # requires the concrete ``VerifiedBlobSet`` via strict digest validation.
    blobs: VerifiedBlobSetView
    operation_id: str
    intent_digest: str
    presented_lease_id: Optional[str]
    audit: AuditMetadata
    typed_command: Optional[Command] = None


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


@dataclass(frozen=True)
class LegacyCommandExecutionResult:
    """Closed legacy command result with a defensive saved projection."""

    decision: Optional[Decision]
    _projection: FrozenJsonObject
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.replayed != (self.decision is None):
            raise ValueError("legacy-command-replay-result-invalid")

    @property
    def projection(self) -> dict:
        return self._projection.thaw()


@dataclass(frozen=True)
class PreparedTransitionOperation:
    """Typed command, inert effects, and adapter response without proposed state."""

    command: Command
    effects: tuple[object, ...]
    result: dict


class MissionInitializer(Protocol):
    """Persistence boundary for the legacy v4 bootstrap transaction."""

    def initialize(self, arguments: object) -> None:
        ...


@runtime_checkable
class MissionReinitializer(MissionInitializer, Protocol):
    """Explicit terminal-session reinitialization boundary."""

    def current_mission(self) -> object:
        ...

    def start_new_mission(self, arguments: object, current: object) -> None:
        ...


class RepositoryReadResult(Protocol):
    """Minimum canonical read view shared by compatibility and v5 storage."""

    state: MissionState


@runtime_checkable
class MissionRepository(Protocol):
    """Common typed command/read port implemented by both repository formats."""

    def read(self, session_id: str) -> RepositoryReadResult:
        ...

    def execute(
        self,
        request: ExecutionRequest,
    ) -> RepositoryExecutionResult:
        ...


@runtime_checkable
class LegacyMissionRepository(MissionRepository, Protocol):
    """Compatibility-only transaction surface; never a recoverable v5 UoW."""

    def transaction(self) -> ContextManager[object]:
        ...

    def load(self) -> dict:
        ...

    @overload
    def execute(
        self,
        command: Command,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> LegacyCommandExecutionResult:
        ...

    @overload
    def execute(
        self,
        request: ExecutionRequest,
    ) -> RepositoryExecutionResult:
        ...

    def execute(
        self,
        command,
        *,
        backup=True,
        administrative=False,
        aggregate_action=None,
    ):
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


@runtime_checkable
class RecoverableUnitOfWork(MissionRepository, Protocol):
    """Stronger v5 repository protocol; legacy v4 must not claim this type."""

    def initialize(
        self, request: ExecutionRequest, *, state_bytes: bytes
    ) -> CommitResult:
        ...

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
