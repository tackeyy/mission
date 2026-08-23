"""Behavior-compatible repository for missing/v1-v4 session documents."""

from __future__ import annotations

import copy
import contextlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, ContextManager, Iterable

from mission_application.artifact import EvidenceDecision, EvidenceEffect, validate_evidence_effect
from mission_application.ports import AggregateIndexError, AuditMetadata
from mission_kernel.json_codec import decode_json_object
from mission_kernel import MissionState, decode_mission_state, project_legacy_document
from enum import Enum

from mission_kernel.model import Phase
from mission_kernel.transitions import (
    Decision,
    TransitionTableError,
    bind_transition_effects,
    decide,
    transition_control_claim_bounds,
)

from .fenced_commit import (
    AdmittedSnapshot,
    CommitResult,
    ExecutionRequest,
    FencedCommitError,
    LocalFencedRepository,
    PendingLease,
    RepositoryExecutionResult,
    admit_lease,
    compute_intent_digest,
    validate_execution_request,
)
from .local_uow import VerifiedBlobSet


_CLAIM_ABSENT = object()


def _apply_transition_claims(transition: object, proposed: dict) -> None:
    """Apply the decided completion-adjacent claims as the persisted values.

    批2-a-2 (#631): the transition's claimed values are what get persisted —
    a claim fills a field the compatibility writer omitted and overwrites an
    equal value it wrote, while a writer that produced a *different* value
    indicates re-implementation drift and fails closed.  Unclaimed fields
    (timing, lease, passthrough, raw halt_reason) stay under the
    compatibility writer's authority until 批2-a-3 removes the dict
    mutations entirely.
    """
    try:
        bounds = transition_control_claim_bounds(transition)
    except TransitionTableError as exc:
        raise FencedCommitError(
            "transition-unsealed",
            "execute requires a transition issued by the canonical decision table",
        ) from exc

    def _projected(value: object) -> object:
        return value.value if isinstance(value, Enum) else value

    def _matches(current: object, expected: object) -> bool:
        if expected is None:
            return current is None or current is _CLAIM_ABSENT
        if isinstance(expected, bool):
            return type(current) is bool and current is expected
        return current == expected

    for field_name, (before, after) in bounds.items():
        current = proposed.get(field_name, _CLAIM_ABSENT)
        expected = _projected(after)
        # 許容されるのは「決定後の値を書いた writer」か「触っていない writer
        # （決定前の値のまま）」だけ。第三の値は再実装 drift として fail-closed。
        if not _matches(current, expected) and not _matches(
            current, _projected(before)
        ):
            raise FencedCommitError(
                "transition-divergence",
                "compatibility mutation diverges from the decided transition"
                " on %s" % field_name,
            )
        if expected is None:
            proposed.pop(field_name, None)
        else:
            proposed[field_name] = expected


@dataclass(frozen=True)
class LegacyRepositorySnapshot:
    """Canonical read result for one legacy compatibility document."""

    state: MissionState
    state_bytes: bytes


class LegacyV4InitializerRepository:
    """Expose the existing atomic v4 bootstrap as a repository operation.

    A1 does not switch new sessions to v5.  The injected writer is therefore
    the compatibility persistence implementation, while the application use
    case and CLI remain independent of its filesystem details.
    """

    def __init__(
        self,
        *,
        initialize_state: Callable[..., None],
        write_state: Callable[[object, object], None],
    ) -> None:
        self._initialize_state = initialize_state
        self._write_state = write_state

    def initialize(self, arguments: object) -> None:
        self._initialize_state(arguments, write_state=self._write_state)


class LegacyV4Repository:
    """Coordinate legacy load/save while keeping ``execute`` pure.

    The injected persistence functions preserve the existing CLI's StateLock,
    atomic writer, backup, and fenced-lease behavior without making the
    application layer depend on the CLI module.
    """

    def __init__(
        self,
        *,
        lock: Callable[[], ContextManager[object]],
        read_state: Callable[[], dict],
        write_state: Callable[..., None],
        backup_state: Callable[[], None],
        add_to_aggregate: Callable[[], None] | None = None,
        remove_from_aggregate: Callable[[], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_ttl_seconds: int = 15 * 60,
        effect_transaction: Callable[
            [tuple[EvidenceEffect, ...]], ContextManager[object]
        ]
        | None = None,
        format_guard: Callable[[], object] | None = None,
    ) -> None:
        self._lock = lock
        self._read_state = read_state
        self._write_state = write_state
        self._backup_state = backup_state
        self._add_to_aggregate = add_to_aggregate
        self._remove_from_aggregate = remove_from_aggregate
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_ttl_seconds = lease_ttl_seconds
        self._effect_transaction = effect_transaction
        self._format_guard = format_guard

    def transaction(self) -> ContextManager[object]:
        return self._lock()

    def load(self) -> dict:
        if self._format_guard is not None:
            self._format_guard()
        return self._read_state()

    def read(self, session_id: str) -> LegacyRepositorySnapshot:
        """Read one legacy session through the common typed repository port."""
        if not isinstance(session_id, str) or not session_id:
            raise FencedCommitError("request-invalid", "session id is invalid")
        with self.transaction():
            document = self.load()
            if not isinstance(document, dict):
                raise FencedCommitError("record-invalid", "legacy state is not an object")
            try:
                source = json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                state = decode_mission_state(source)
            except (TypeError, ValueError, UnicodeError) as exc:
                raise FencedCommitError(
                    getattr(exc, "code", "record-invalid"),
                    "legacy state cannot be decoded",
                ) from exc
            if state.identity.session_id not in {None, session_id}:
                raise FencedCommitError("lineage-mismatch", "legacy session differs")
            return LegacyRepositorySnapshot(state=state, state_bytes=source)

    def execute(self, state, mutation=None, transition=None):
        """Return the proposed v4 document without performing any I/O."""
        if isinstance(state, ExecutionRequest):
            if mutation is not None or transition is not None:
                raise FencedCommitError(
                    "request-invalid", "typed execution does not accept a decision callback"
                )
            return self._execute_request(state)
        # The typed transition is the authority decision, while the A1
        # compatibility reducer remains the writer for legacy timing, lease,
        # and passthrough fields.  Projecting the canonical state here would
        # pre-apply the phase and lose the legacy reducer's duration boundary.
        # A supplied transition's claims are applied as the persisted values,
        # with writer divergence failing closed (批2-a-1 #630 / 批2-a-2 #631).
        proposed = copy.deepcopy(state)
        mutation(proposed)
        if transition is not None:
            _apply_transition_claims(transition, proposed)
        return proposed

    def _execute_request(
        self,
        request: ExecutionRequest,
    ) -> RepositoryExecutionResult:
        """Evaluate the common typed request while retaining the v4 layout."""
        validate_execution_request(request)
        if request.typed_command is None:
            raise FencedCommitError("request-invalid", "typed command is required")
        with self.transaction():
            current = self.load()
            if not isinstance(current, dict):
                raise FencedCommitError("record-invalid", "legacy state is not an object")
            source = json.dumps(
                current,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            try:
                state = decode_mission_state(source)
            except (TypeError, ValueError, UnicodeError) as exc:
                raise FencedCommitError(
                    getattr(exc, "code", "record-invalid"),
                    "legacy state cannot be decoded",
                ) from exc
            if state.identity.session_id not in {None, request.session_id}:
                raise FencedCommitError("lineage-mismatch", "legacy session differs")
            pending = admit_lease(
                request,
                state.lease,
                self._clock(),
                self._lease_ttl_seconds,
            )
            admitted_state = replace(
                state,
                lease=pending.target,
                snapshot_provenance=None,
            )
            decision = decide(admitted_state, request.typed_command)
            if not isinstance(decision, Decision):
                raise FencedCommitError("decision-invalid", "decision result type is invalid")
            if not decision.accepted:
                if decision.transition is not None or decision.rejection is None:
                    raise FencedCommitError("decision-invalid", "rejected decision is not closed")
                return RepositoryExecutionResult(False, None, decision.rejection.code)
            if decision.transition is None or decision.rejection is not None:
                raise FencedCommitError("decision-invalid", "accepted decision is not closed")
            event_types = tuple(event.type for event in decision.events)
            if event_types != request.audit.event_types:
                raise FencedCommitError(
                    "audit-binding-mismatch", "audit event categories differ"
                )
            transition = decision.transition
            if request.blobs.blobs:
                transition = bind_transition_effects(
                    transition,
                    tuple(blob.binding for blob in request.blobs.blobs),
                )
            target = transition.new_state
            if target.lease != pending.target:
                raise FencedCommitError(
                    "pending-lease-mismatch", "transition does not contain the pending lease"
                )
            proposed = json.loads(project_legacy_document(target))
            bindings = transition.effects
            if type(bindings) is not tuple:
                raise FencedCommitError(
                    "effect-binding-invalid", "transition effects are not immutable"
                )
            expected = tuple(blob.binding for blob in request.blobs.blobs)
            if bindings != expected:
                raise FencedCommitError(
                    "blob-binding-mismatch", "transition effects differ from captured blobs"
                )
            if bindings:
                if self._effect_transaction is None:
                    raise FencedCommitError(
                        "effect-binding-invalid",
                        "effectful typed request has no publication transaction",
                    )
                effects = tuple(
                    EvidenceEffect(
                        kind=blob.binding.kind,
                        target=blob.binding.relative_path,
                        content=blob.content,
                        digest=blob.binding.digest,
                        size=blob.binding.size,
                    )
                    for blob in request.blobs.blobs
                )
                closed = self.validate_effects(effects)
                with self._effect_transaction(closed):
                    self.save(proposed)
            else:
                self.save(proposed)
            # The compatibility writer has no immutable commit/head record from
            # which a v5 CommitResult could be derived.  Successful v4 writes
            # therefore report acceptance with commit=None by design.
            return RepositoryExecutionResult(True, None, None)

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
        if self._format_guard is not None:
            self._format_guard()
        if backup:
            self._backup_state()
        # Preserve the legacy writer call shape.  Several callers replace the
        # writer with a one-argument fault injector; the administrative flag
        # was only ever supplied for the routed-goal path.
        if administrative:
            self._write_state(state, administrative=True)
        else:
            self._write_state(state)
        callback = {
            None: None,
            "add": self._add_to_aggregate,
            "remove": self._remove_from_aggregate,
        }.get(aggregate_action)
        if aggregate_action not in {None, "add", "remove"}:
            raise ValueError("unknown aggregate action")
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            raise AggregateIndexError(str(exc)) from exc

    @staticmethod
    def validate_effects(effects: Iterable[EvidenceEffect]) -> tuple[EvidenceEffect, ...]:
        """Close every inert effect before the first public byte is emitted."""
        if not isinstance(effects, tuple):
            raise ValueError("effect-binding-invalid")
        validated = tuple(validate_evidence_effect(effect) for effect in effects)
        targets = [effect.target for effect in validated]
        if len(targets) != len(set(targets)):
            raise ValueError("effect-target-duplicated")
        return validated

    def execute_effects(
        self,
        decide: Callable[[dict], EvidenceDecision],
        *,
        effect_transaction: Callable[[tuple[EvidenceEffect, ...]], ContextManager[object]],
        bind_published: Callable[[EvidenceDecision, object], None] | None = None,
        backup: bool = True,
    ) -> EvidenceDecision:
        """Run one lease-first v4 evidence transaction with all-or-none effects.

        ``load`` is intentionally called before the effect transaction starts;
        callers bind the current fenced-lease check to that load.  The effect
        context therefore sees no request after a rejected lease or decision,
        and rolls published files back if binding or state save fails.
        """
        with self.transaction():
            current = self.load()
            decision = decide(copy.deepcopy(current))
            if not isinstance(decision, EvidenceDecision) or not isinstance(decision.state, dict):
                raise ValueError("effect-decision-invalid")
            effects = self.validate_effects(decision.effects)
            with effect_transaction(effects) as published:
                if bind_published is not None:
                    bind_published(decision, published)
                self.save(decision.state, backup=backup)
            return decision


class V5CompatibilityRepository:
    """Run extracted compatibility reducers on one format-pinned v5 UoW.

    The reducer still receives the v4-shaped projection expected by the A1-A5
    application seams, but publication is a single v5 generation.  No legacy
    session file or backup is written.
    """

    def __init__(
        self,
        *,
        repository: LocalFencedRepository,
        session_id: str,
        lease_owner_session_id: str,
        presented_lease_id: str | None,
        prepare_state: Callable[[dict], dict] | None = None,
        add_to_aggregate: Callable[[], None] | None = None,
        remove_from_aggregate: Callable[[], None] | None = None,
        lease_committed: Callable[[PendingLease, dict], None] | None = None,
        format_guard: Callable[[], object] | None = None,
        operation_id: str | None = None,
        operation_command: object | None = None,
        operation_command_type: str | None = None,
    ) -> None:
        self._repository = repository
        self._session_id = session_id
        self._lease_owner_session_id = lease_owner_session_id
        self._presented_lease_id = presented_lease_id
        self._prepare_state = prepare_state or (lambda state: state)
        self._add_to_aggregate = add_to_aggregate
        self._remove_from_aggregate = remove_from_aggregate
        self._lease_committed = lease_committed
        self._format_guard = format_guard
        if (operation_id is None) != (operation_command is None):
            raise ValueError("operation id and command must be supplied together")
        if operation_id is not None and not operation_command_type:
            raise ValueError("operation command type is required")
        self._operation_id = operation_id
        self._operation_command = operation_command
        self._operation_command_type = operation_command_type
        self._admitted: AdmittedSnapshot | None = None
        self._replayed: CommitResult | None = None
        self._transaction_active = False

    @contextlib.contextmanager
    def transaction(self):
        if self._transaction_active:
            raise FencedCommitError("request-invalid", "nested v5 transaction")
        self._transaction_active = True
        try:
            yield
        finally:
            self._admitted = None
            self._replayed = None
            self._transaction_active = False

    def _request(self) -> ExecutionRequest:
        operation_id = self._operation_id or "compat:" + secrets.token_hex(16)
        command = self._operation_command
        if command is None:
            command = decode_json_object(
                json.dumps(
                    {
                        "schema": "mission-command-intent/1",
                        "type": "compatibility-mutation",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        command_type = self._operation_command_type or "compatibility-mutation"
        blobs = VerifiedBlobSet(())
        return ExecutionRequest(
            session_id=self._session_id,
            lease_owner_session_id=self._lease_owner_session_id,
            command=command,
            blobs=blobs,
            operation_id=operation_id,
            intent_digest=compute_intent_digest(
                session_id=self._session_id,
                lease_owner_session_id=self._lease_owner_session_id,
                operation_id=operation_id,
                command=command,
                blobs=blobs,
            ),
            presented_lease_id=self._presented_lease_id,
            audit=AuditMetadata(command_type, ()),
        )

    @property
    def operation_replayed(self) -> bool:
        return self._replayed is not None

    def load(self) -> dict:
        if not self._transaction_active:
            raise FencedCommitError(
                "request-invalid",
                "v5 load requires an active transaction",
            )
        if self._admitted is not None:
            raise FencedCommitError("request-invalid", "v5 transaction already loaded")
        if self._format_guard is not None:
            self._format_guard()
        admitted = self._repository.begin(self._request())
        if isinstance(admitted, CommitResult):
            snapshot = self._repository.read(self._session_id)
            self._replayed = admitted
            return json.loads(project_legacy_document(snapshot.state))
        if admitted.base is None:
            raise FencedCommitError("initial-state-required", "v5 head is missing")
        self._admitted = admitted
        admitted_state = replace(
            admitted.base.state,
            lease=admitted.pending_lease.target,
            snapshot_provenance=None,
        )
        return json.loads(project_legacy_document(admitted_state))

    def read(self, session_id: str):
        return self._repository.read(session_id)

    def execute(self, state, mutation=None, transition=None):
        if isinstance(state, ExecutionRequest):
            return self._repository.execute(state)
        proposed = copy.deepcopy(state)
        mutation(proposed)
        if transition is not None:
            _apply_transition_claims(transition, proposed)
        return proposed

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
        del backup, administrative
        if self._format_guard is not None:
            self._format_guard()
        if self._replayed is not None:
            return
        admitted = self._admitted
        if admitted is None:
            raise FencedCommitError("request-invalid", "v5 transaction was not loaded")
        proposed = self._prepare_state(copy.deepcopy(state))
        state_bytes = json.dumps(
            proposed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        prepared = self._repository._stage_persistence(
            admitted,
            state_bytes=state_bytes,
            effects=(),
        )
        self._repository.commit(prepared, prepared.precondition)
        self._admitted = None
        if self._lease_committed is not None:
            self._lease_committed(admitted.pending_lease, proposed)
        callback = {
            None: None,
            "add": self._add_to_aggregate,
            "remove": self._remove_from_aggregate,
        }.get(aggregate_action)
        if aggregate_action not in {None, "add", "remove"}:
            raise ValueError("unknown aggregate action")
        if callback is not None:
            try:
                callback()
            except Exception as exc:
                raise AggregateIndexError(str(exc)) from exc

    validate_effects = staticmethod(LegacyV4Repository.validate_effects)
