"""Behavior-compatible repository for missing/v1-v4 session documents."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, ContextManager, Iterable

from mission_application.artifact import EvidenceDecision, EvidenceEffect, validate_evidence_effect
from mission_application.ports import AggregateIndexError
from mission_kernel import decode_mission_state, project_legacy_document
from mission_kernel.transitions import Decision

from .fenced_commit import (
    ExecutionRequest,
    FencedCommitError,
    RepositoryExecutionResult,
    _admit_lease,
    _validate_request,
)


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

    def transaction(self) -> ContextManager[object]:
        return self._lock()

    def load(self) -> dict:
        return self._read_state()

    def execute(self, state, mutation, transition=None):
        """Return the proposed v4 document without performing any I/O."""
        if isinstance(state, ExecutionRequest):
            return self._execute_request(state, mutation)
        # The typed transition is the authority decision, while the A1
        # compatibility reducer remains the writer for legacy timing, lease,
        # and passthrough fields.  Projecting the canonical state here would
        # pre-apply the phase and lose the legacy reducer's duration boundary.
        proposed = copy.deepcopy(state)
        mutation(proposed)
        return proposed

    def _execute_request(
        self,
        request: ExecutionRequest,
        decide_command: Callable[[object], Decision],
    ) -> RepositoryExecutionResult:
        """Evaluate the common typed request while retaining the v4 layout."""
        _validate_request(request)
        if not callable(decide_command):
            raise FencedCommitError("request-invalid", "decision function is not callable")
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
            except Exception as exc:
                raise FencedCommitError(
                    getattr(exc, "code", "record-invalid"),
                    "legacy state cannot be decoded",
                ) from exc
            if state.identity.session_id not in {None, request.session_id}:
                raise FencedCommitError("lineage-mismatch", "legacy session differs")
            pending = _admit_lease(
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
            decision = decide_command(admitted_state)
            if not isinstance(decision, Decision):
                raise FencedCommitError("decision-invalid", "decision result type is invalid")
            if not decision.accepted:
                if decision.transition is not None or decision.rejection is None:
                    raise FencedCommitError("decision-invalid", "rejected decision is not closed")
                return RepositoryExecutionResult(False, None, decision.rejection.code)
            if decision.transition is None or decision.rejection is not None:
                raise FencedCommitError("decision-invalid", "accepted decision is not closed")
            target = decision.transition.new_state
            if target.lease != pending.target:
                raise FencedCommitError(
                    "pending-lease-mismatch", "transition does not contain the pending lease"
                )
            proposed = json.loads(project_legacy_document(target))
            bindings = decision.transition.effects
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
            return RepositoryExecutionResult(True, None, None)

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
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
