"""Behavior-compatible repository for missing/v1-v4 session documents."""

from __future__ import annotations

import copy
import contextlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, ContextManager, Iterable

from mission_application.artifact import (
    EvidenceDecision,
    EvidenceEffect,
    PreparedArtifactOperation,
    validate_evidence_effect,
)
from mission_application.evidence import PreparedEvidenceOperation
from mission_application.ports import (
    AggregateIndexError,
    AuditMetadata,
    LegacyCommandExecutionResult,
    PreparedTransitionOperation,
)
from mission_kernel.commands import (
    AdvancePhase,
    CompatibilityPayload,
    MarkPass,
    Reactivate,
    ResumeStale,
    kernel_command_type,
)
from mission_kernel.json_codec import decode_json_object
from mission_kernel import MissionState, decode_mission_state, project_legacy_document

from mission_kernel.model import BoundScore, FrozenJsonObject, HaltCategory, Phase
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.transitions import (
    Decision,
    bind_transition_effects,
    decide,
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


def _legacy_command_state(document: dict, command: object) -> MissionState:
    """Build the narrow decision view bound to the exact loaded document."""
    compatible = copy.deepcopy(document)
    normalize_reactivation_category = False
    plan = compatible.get("canonical_plan")
    if isinstance(plan, dict):
        plan.setdefault("schema", "mission-plan/1")
        plan.setdefault("source_digest", plan.get("digest"))
        plan.setdefault(
            "validated_at",
            compatible.get("updated_at") or compatible.get("started_at"),
        )
    if isinstance(command, Reactivate):
        raw_category = compatible.get("halt_category")
        if not isinstance(raw_category, str) or raw_category not in {
            item.value for item in HaltCategory
        }:
            if command.expected_category is not HaltCategory.OTHER:
                raise FencedCommitError(
                    "decision-input-invalid", "legacy halt category does not match command"
                )
            normalize_reactivation_category = True
            compatible.pop("halt_category", None)
    if isinstance(command, ResumeStale):
        reason = compatible.get("halt_reason")
        category = compatible.get("halt_category")
        legacy_stale = (
            category in (None, "", "unknown")
            and isinstance(reason, str)
            and reason.startswith(("orphan:", "stale:"))
        )
        if category != "stale" and not legacy_stale:
            raise FencedCommitError(
                "decision-input-invalid", "legacy state is not stale"
            )
        compatible["phase"] = "halted"
        compatible["loop_active"] = False
        compatible["halt_category"] = "stale"
        compatible["terminal_outcome"] = "stale_superseded"
    try:
        source = json.dumps(
            compatible,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        state = decode_mission_state(source)
        passthrough = freeze_json_value(document)
        assert isinstance(passthrough, FrozenJsonObject)
        if normalize_reactivation_category:
            state = replace(
                state,
                control=replace(
                    state.control, halt_category=HaltCategory.OTHER
                ),
                legacy_passthrough=passthrough,
            )
        else:
            state = replace(state, legacy_passthrough=passthrough)
        return state
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FencedCommitError(
            getattr(exc, "code", "record-invalid"),
            "legacy state cannot be decoded",
        ) from exc


_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "project_root",
        "pid",
        "pid_source",
        "hostname",
        "session_id",
        "agent",
        "created_at_session",
        "cli_version",
    }
)


def _command_with_missing_metadata(
    command: object,
    document: dict,
    metadata: dict,
) -> object:
    if not metadata:
        return command
    if set(metadata) - _METADATA_FIELDS:
        raise FencedCommitError("request-invalid", "metadata field is not closed")
    if not hasattr(command, "compatibility"):
        return command
    compatibility = command.compatibility
    if not isinstance(compatibility, CompatibilityPayload):
        raise FencedCommitError("request-invalid", "command compatibility is invalid")
    upserts = compatibility.upserts.thaw()
    for key, value in metadata.items():
        if key not in document:
            upserts[key] = copy.deepcopy(value)
    return replace(
        command,
        compatibility=CompatibilityPayload(upserts, compatibility.removals),
    )


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
    """Atomically decide, project, and persist typed legacy commands.

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
        aggregate_recover: Callable[[], None] | None = None,
        aggregate_prepare: Callable[[str], object] | None = None,
        aggregate_finalize: Callable[[object], None] | None = None,
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
        self._aggregate_recover = aggregate_recover
        self._aggregate_prepare = aggregate_prepare
        self._aggregate_finalize = aggregate_finalize
        aggregate_callbacks = (
            aggregate_recover,
            aggregate_prepare,
            aggregate_finalize,
        )
        if any(item is not None for item in aggregate_callbacks) and not all(
            callable(item) for item in aggregate_callbacks
        ):
            raise ValueError("aggregate coordinator callbacks must be supplied together")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_ttl_seconds = lease_ttl_seconds
        self._effect_transaction = effect_transaction
        self._format_guard = format_guard
        self._callback_depth = 0
        self._transaction_depth = 0
        self._loaded_document: dict | None = None

    @contextlib.contextmanager
    def transaction(self):
        # Nested callers share one exact loaded document until the outermost
        # transaction exits; only the outer boundary clears that snapshot.
        self._transaction_depth += 1
        if self._transaction_depth == 1:
            self._loaded_document = None
        try:
            with self._guarded_context(self._lock):
                yield
        finally:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self._loaded_document = None


    # 外部から注入される callable の信頼境界（#632 / Sol 4 巡目）。
    #
    # ``decision`` / ``pre-decision`` の 2 分類は persistence への再入を許さない。
    # ``primitive`` は「呼ばれた時点で決定が確定しており、再入させても検証を
    # 迂回できない」ものだけを置く。分類の網羅は inventory テストが固定する。
    GUARDED_INJECTED_CALLABLES = (
        "_format_guard",
        "_clock",
        "_read_state",
        "_backup_state",
        "_write_state",
        "_add_to_aggregate",
        "_remove_from_aggregate",
        "_aggregate_recover",
        "_aggregate_prepare",
        "_aggregate_finalize",
        "_lock",
        "_effect_transaction",
    )

    @contextlib.contextmanager
    def _callback_guard(self):
        """Reject persistence entry points while a caller callback is running.

        直接の ``save()`` だけを塞ぐと、typed request や effect callback、注入 hook
        という別の実行入口から同じ不変条件を迂回できる（#632 の Sol 指摘）。
        callback 実行中は ``execute`` / ``save`` / ``execute_effects`` を拒否する。
        """
        self._callback_depth += 1
        try:
            yield
        finally:
            self._callback_depth -= 1

    def _guarded_call(self, callback, *args, **kwargs):
        """Run one injected callable inside the re-entrancy boundary."""
        with self._callback_guard():
            return callback(*args, **kwargs)

    def _reject_reentrant_entry(self, operation: str) -> None:
        if self._callback_depth:
            raise FencedCommitError(
                "request-invalid",
                "%s is not allowed while a decision is being executed" % operation,
            )

    @contextlib.contextmanager
    def _guarded_context(self, factory, *args):
        """Hold the guard across a caller-supplied context manager's own hooks.

        factory / ``__enter__`` / ``__exit__`` は外部 callback なので囲む。本文
        （内部の ``save`` 等）はガードの外で実行しなければならないため、enter と
        exit だけを個別に囲む。特殊メソッドは ``with`` 文と同じく **型から
        ``__enter__`` 実行前に取得**し、instance 差し替えで意味論が変わらないように
        する（#632 / Sol 4 巡目 Medium）。
        """
        with self._callback_guard():
            manager = factory(*args)
            manager_type = type(manager)
            enter = manager_type.__enter__
            exit_ = manager_type.__exit__
            entered = enter(manager)
        try:
            yield entered
        except BaseException as error:
            with self._callback_guard():
                # truth-value 評価（`__bool__`）も外部 callback なのでガード内で
                # 済ませる。外に出すと `__bool__` から persistence へ再入できる
                # （#632 / Sol 5 巡目の High）。
                suppressed = bool(exit_(manager, type(error), error, error.__traceback__))
            if not suppressed:
                raise
        else:
            with self._callback_guard():
                exit_(manager, None, None, None)

    def load(self) -> dict:
        if self._format_guard is not None:
            self._guarded_call(self._format_guard)
        document = self._guarded_call(self._read_state)
        if not isinstance(document, dict):
            return document
        self._loaded_document = copy.deepcopy(document)
        return copy.deepcopy(document)

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

    def execute(
        self,
        command,
        *,
        backup=True,
        administrative=False,
        aggregate_action=None,
    ):
        """Decide, project, and persist one legacy command atomically."""
        self._reject_reentrant_entry("execute")
        if isinstance(command, ExecutionRequest):
            return self._execute_request(command)
        try:
            kernel_command_type(command)
        except TypeError as exc:
            raise FencedCommitError("request-invalid", "typed command is required") from exc
        if self._transaction_depth <= 0 or self._loaded_document is None:
            raise FencedCommitError(
                "request-invalid", "legacy command execute requires one active loaded transaction"
            )
        state = _legacy_command_state(self._loaded_document, command)
        decision = decide(state, command)
        if not isinstance(decision, Decision):
            raise FencedCommitError("decision-invalid", "decision result type is invalid")
        if not decision.accepted:
            if decision.transition is not None or decision.rejection is None:
                raise FencedCommitError("decision-invalid", "rejected decision is not closed")
            frozen = freeze_json_value(self._loaded_document)
            assert isinstance(frozen, FrozenJsonObject)
            return LegacyCommandExecutionResult(decision, frozen)
        if decision.transition is None or decision.rejection is not None:
            raise FencedCommitError("decision-invalid", "accepted decision is not closed")
        proposed = json.loads(project_legacy_document(decision.transition.new_state))
        frozen = freeze_json_value(proposed)
        assert isinstance(frozen, FrozenJsonObject)
        result = LegacyCommandExecutionResult(decision, frozen)
        try:
            self.save(
                proposed,
                backup=backup,
                administrative=administrative,
                aggregate_action=aggregate_action,
            )
        except AggregateIndexError as exc:
            raise AggregateIndexError(str(exc), execution=result) from exc
        return result

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
                self._guarded_call(self._clock),
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
                with self._guarded_context(self._effect_transaction, closed):
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
        self._reject_reentrant_entry("save")
        if aggregate_action not in {None, "add", "remove"}:
            raise ValueError("unknown aggregate action")
        if self._format_guard is not None:
            self._guarded_call(self._format_guard)
        prepared_intent = None
        if self._aggregate_recover is not None:
            self._guarded_call(self._aggregate_recover)
        if aggregate_action is not None and self._aggregate_prepare is not None:
            prepared_intent = self._guarded_call(
                self._aggregate_prepare, aggregate_action
            )
        try:
            if backup:
                self._guarded_call(self._backup_state)
            # Preserve the legacy writer call shape.  Several callers replace the
            # writer with a one-argument fault injector; the administrative flag
            # was only ever supplied for the routed-goal path.
            if administrative:
                self._guarded_call(self._write_state, state, administrative=True)
            else:
                self._guarded_call(self._write_state, state)
        except BaseException:
            if prepared_intent is not None and self._aggregate_recover is not None:
                with contextlib.suppress(Exception):
                    self._guarded_call(self._aggregate_recover)
            raise
        if aggregate_action is None:
            return
        if prepared_intent is not None:
            try:
                self._guarded_call(self._aggregate_finalize, prepared_intent)
            except Exception as exc:
                raise AggregateIndexError(str(exc)) from exc
            return
        # attribute を guard の第 1 引数へ直接渡す（dict 収集の alias を作ると
        # 静的な境界検査が per-variable の追跡を要求されるため。Sol 7 巡目）。
        try:
            if aggregate_action == "add":
                self._guarded_call(self._add_to_aggregate)
            else:
                self._guarded_call(self._remove_from_aggregate)
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
        self._reject_reentrant_entry("execute_effects")
        with self.transaction():
            current = self.load()
            with self._callback_guard():
                decision = decide(copy.deepcopy(current))
            if not isinstance(decision, EvidenceDecision) or not isinstance(decision.state, dict):
                raise ValueError("effect-decision-invalid")
            effects = self.validate_effects(decision.effects)
            with self._guarded_context(effect_transaction, effects) as published:
                if bind_published is not None:
                    with self._callback_guard():
                        bind_published(decision, published)
                self.save(decision.state, backup=backup)
            return decision

    def execute_transition_effects(
        self,
        prepare: Callable[
            [dict], PreparedArtifactOperation | PreparedTransitionOperation
        ],
        *,
        effect_transaction: Callable[
            [tuple[EvidenceEffect, ...]], ContextManager[object]
        ]
        | None = None,
        verify_published: Callable[[tuple[EvidenceEffect, ...], object], None]
        | None = None,
        backup: bool = True,
    ) -> tuple[
        PreparedArtifactOperation | PreparedTransitionOperation,
        LegacyCommandExecutionResult,
    ]:
        """Compatibility wrapper over the shared typed evidence core."""

        def publish(_prepared, effects):
            # 明示引数のみをここで包む。省略時の fallback（self._effect_transaction）
            # は shared core の guarded 分岐に任せ、注入 callable の alias を作らない
            # （#626 の境界検査は alias 経由の参照も検出する）。
            assert effect_transaction is not None
            return effect_transaction(effects)

        def verify(_prepared, effects, published):
            if verify_published is not None:
                return verify_published(effects, published)
            if published != effects:
                raise ValueError("published-artifact-effect-binding-invalid")

        return self.execute_evidence_transition_effects(
            prepare,
            operation_type=(PreparedArtifactOperation, PreparedTransitionOperation),
            operation_error="transition-operation-invalid",
            effect_transaction=publish if effect_transaction is not None else None,
            verify_published=verify,
            backup=backup,
        )

    def execute_evidence_transition_effects(
        self,
        prepare: Callable[[dict], object],
        *,
        operation_type: type = PreparedEvidenceOperation,
        operation_error: str = "evidence-operation-invalid",
        effect_transaction: Callable[[object, tuple[EvidenceEffect, ...]], ContextManager[object]]
        | None = None,
        verify_published: Callable[[object, tuple[EvidenceEffect, ...], object], None]
        | None = None,
        backup: bool = True,
    ) -> tuple[object, LegacyCommandExecutionResult]:
        """Prepare, decide, publish, verify, and project one typed evidence command."""
        self._reject_reentrant_entry("execute_evidence_transition_effects")
        with self.transaction():
            current = self.load()
            with self._callback_guard():
                prepared = prepare(copy.deepcopy(current))
            if not isinstance(prepared, operation_type):
                raise ValueError(operation_error)
            state = _legacy_command_state(current, prepared.command)
            decision = decide(state, prepared.command)
            if not isinstance(decision, Decision):
                raise FencedCommitError(
                    "decision-invalid", "decision result type is invalid"
                )
            if not decision.accepted:
                if decision.transition is not None or decision.rejection is None:
                    raise FencedCommitError(
                        "decision-invalid", "rejected decision is not closed"
                    )
                frozen_current = freeze_json_value(current)
                assert isinstance(frozen_current, FrozenJsonObject)
                return prepared, LegacyCommandExecutionResult(
                    decision, frozen_current
                )
            if decision.transition is None or decision.rejection is not None:
                raise FencedCommitError(
                    "decision-invalid", "accepted decision is not closed"
                )
            effects = self.validate_effects(prepared.effects)
            transition = bind_transition_effects(decision.transition, effects)
            bound_decision = replace(decision, transition=transition)
            proposed = json.loads(project_legacy_document(transition.new_state))
            frozen = freeze_json_value(proposed)
            assert isinstance(frozen, FrozenJsonObject)
            execution = LegacyCommandExecutionResult(bound_decision, frozen)
            if effects:
                if effect_transaction is None:
                    if self._effect_transaction is None:
                        raise ValueError("evidence-effect-transaction-missing")
                    publication = self._guarded_context(
                        self._effect_transaction, effects, prepared
                    )
                else:
                    publication = self._guarded_context(
                        effect_transaction, prepared, effects
                    )
                with publication as published:
                    if verify_published is not None:
                        with self._callback_guard():
                            verify_published(prepared, effects, published)
                    elif published != effects:
                        raise ValueError("published-evidence-effect-binding-invalid")
                    self.save(proposed, backup=backup)
            else:
                self.save(proposed, backup=backup)
            return prepared, execution


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
        metadata: dict | None = None,
        add_to_aggregate: Callable[[], None] | None = None,
        remove_from_aggregate: Callable[[], None] | None = None,
        aggregate_recover: Callable[[], None] | None = None,
        aggregate_prepare: Callable[[str], object] | None = None,
        aggregate_finalize: Callable[[object], None] | None = None,
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
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata is not an object")
        metadata_value = metadata or {}
        if set(metadata_value) - _METADATA_FIELDS:
            raise ValueError("metadata field is not closed")
        try:
            frozen_metadata = freeze_json_value(metadata_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata is not strict JSON") from exc
        if not isinstance(frozen_metadata, FrozenJsonObject):
            raise ValueError("metadata is not an object")
        self._metadata = frozen_metadata.thaw()
        self._add_to_aggregate = add_to_aggregate
        self._remove_from_aggregate = remove_from_aggregate
        self._aggregate_recover = aggregate_recover
        self._aggregate_prepare = aggregate_prepare
        self._aggregate_finalize = aggregate_finalize
        aggregate_callbacks = (
            aggregate_recover,
            aggregate_prepare,
            aggregate_finalize,
        )
        if any(item is not None for item in aggregate_callbacks) and not all(
            callable(item) for item in aggregate_callbacks
        ):
            raise ValueError("aggregate coordinator callbacks must be supplied together")
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
        self._callback_depth = 0
        self._loaded_document: dict | None = None

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
            self._loaded_document = None
            self._transaction_active = False


    # 外部から注入される callable の信頼境界（#632 / Sol 4 巡目）。分類の網羅は
    # inventory テストが固定する。
    GUARDED_INJECTED_CALLABLES = (
        "_format_guard",
        "_add_to_aggregate",
        "_remove_from_aggregate",
        "_aggregate_recover",
        "_aggregate_prepare",
        "_aggregate_finalize",
        "_lease_committed",
    )

    @contextlib.contextmanager
    def _callback_guard(self):
        """Reject persistence entry points while a caller callback is running."""
        self._callback_depth += 1
        try:
            yield
        finally:
            self._callback_depth -= 1

    def _guarded_call(self, callback, *args, **kwargs):
        """Run one injected callable inside the re-entrancy boundary."""
        with self._callback_guard():
            return callback(*args, **kwargs)

    def _reject_reentrant_entry(self, operation: str) -> None:
        if self._callback_depth:
            raise FencedCommitError(
                "request-invalid",
                "%s is not allowed while a decision is being executed" % operation,
            )

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
            self._guarded_call(self._format_guard)
        admitted = self._repository.begin(self._request())
        if isinstance(admitted, CommitResult):
            snapshot = self._repository.read(self._session_id)
            self._replayed = admitted
            document = json.loads(project_legacy_document(snapshot.state))
            self._loaded_document = copy.deepcopy(document)
            return copy.deepcopy(document)
        if admitted.base is None:
            raise FencedCommitError("initial-state-required", "v5 head is missing")
        self._admitted = admitted
        admitted_state = replace(
            admitted.base.state,
            lease=admitted.pending_lease.target,
            snapshot_provenance=None,
        )
        document = json.loads(project_legacy_document(admitted_state))
        self._loaded_document = copy.deepcopy(document)
        return copy.deepcopy(document)

    def read(self, session_id: str):
        return self._repository.read(session_id)

    def execute(
        self,
        command,
        *,
        backup=True,
        administrative=False,
        aggregate_action=None,
    ):
        del backup
        self._reject_reentrant_entry("execute")
        if isinstance(command, ExecutionRequest):
            return self._repository.execute(command)
        try:
            kernel_command_type(command)
        except TypeError as exc:
            raise FencedCommitError("request-invalid", "typed command is required") from exc
        if not self._transaction_active or self._loaded_document is None:
            raise FencedCommitError(
                "request-invalid", "v5 command execute requires one active loaded transaction"
            )
        command = _command_with_missing_metadata(
            command, self._loaded_document, self._metadata
        )
        state = _legacy_command_state(self._loaded_document, command)
        decision = decide(state, command)
        if not isinstance(decision, Decision):
            raise FencedCommitError("decision-invalid", "decision result type is invalid")
        if not decision.accepted:
            if decision.transition is not None or decision.rejection is None:
                raise FencedCommitError("decision-invalid", "rejected decision is not closed")
            frozen = freeze_json_value(self._loaded_document)
            assert isinstance(frozen, FrozenJsonObject)
            return LegacyCommandExecutionResult(decision, frozen)
        if decision.transition is None or decision.rejection is not None:
            raise FencedCommitError("decision-invalid", "accepted decision is not closed")
        proposed = json.loads(project_legacy_document(decision.transition.new_state))
        frozen = freeze_json_value(proposed)
        assert isinstance(frozen, FrozenJsonObject)
        result = LegacyCommandExecutionResult(decision, frozen)
        try:
            self.save(
                proposed,
                administrative=administrative,
                aggregate_action=aggregate_action,
            )
        except AggregateIndexError as exc:
            raise AggregateIndexError(str(exc), execution=result) from exc
        return result

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
        del backup, administrative
        self._reject_reentrant_entry("save")
        if aggregate_action not in {None, "add", "remove"}:
            raise ValueError("unknown aggregate action")
        if self._format_guard is not None:
            self._guarded_call(self._format_guard)
        proposed = copy.deepcopy(state)
        for key, value in self._metadata.items():
            proposed.setdefault(key, copy.deepcopy(value))
        if self._replayed is not None:
            return
        admitted = self._admitted
        if admitted is None:
            raise FencedCommitError("request-invalid", "v5 transaction was not loaded")
        state_bytes = json.dumps(
            proposed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        prepared_intent = None
        if self._aggregate_recover is not None:
            self._guarded_call(self._aggregate_recover)
        if aggregate_action is not None and self._aggregate_prepare is not None:
            prepared_intent = self._guarded_call(
                self._aggregate_prepare, aggregate_action
            )
        try:
            prepared = self._repository._stage_persistence(
                admitted,
                state_bytes=state_bytes,
                effects=(),
            )
            self._repository.commit(prepared, prepared.precondition)
            self._admitted = None
            if self._lease_committed is not None:
                self._guarded_call(
                    self._lease_committed, admitted.pending_lease, proposed
                )
        except BaseException:
            if prepared_intent is not None and self._aggregate_recover is not None:
                with contextlib.suppress(Exception):
                    self._guarded_call(self._aggregate_recover)
            raise
        if aggregate_action is not None:
            if prepared_intent is not None:
                try:
                    self._guarded_call(self._aggregate_finalize, prepared_intent)
                except Exception as exc:
                    raise AggregateIndexError(str(exc)) from exc
                return
            try:
                if aggregate_action == "add":
                    self._guarded_call(self._add_to_aggregate)
                else:
                    self._guarded_call(self._remove_from_aggregate)
            except Exception as exc:
                raise AggregateIndexError(str(exc)) from exc

    def execute_transition_effects(
        self,
        prepare: Callable[
            [dict], PreparedArtifactOperation | PreparedTransitionOperation
        ],
        *,
        effect_transaction: object | None = None,
        verify_published: object | None = None,
        backup: bool = True,
    ) -> tuple[
        PreparedArtifactOperation | PreparedTransitionOperation,
        LegacyCommandExecutionResult,
    ]:
        """Reuse the typed transition executor for effect-free v5 commands."""
        del effect_transaction, verify_published, backup
        self._reject_reentrant_entry("execute_transition_effects")
        with self.transaction():
            current = self.load()
            with self._callback_guard():
                prepared = prepare(copy.deepcopy(current))
            if not isinstance(
                prepared, (PreparedArtifactOperation, PreparedTransitionOperation)
            ):
                raise ValueError("transition-operation-invalid")
            effects = self.validate_effects(prepared.effects)
            if effects:
                raise ValueError("v5-transition-effects-not-supported")
            if self.operation_replayed:
                frozen = freeze_json_value(current)
                assert isinstance(frozen, FrozenJsonObject)
                return prepared, LegacyCommandExecutionResult(
                    None, frozen, replayed=True
                )
            execution = self.execute(prepared.command)
            if not isinstance(execution, LegacyCommandExecutionResult):
                raise FencedCommitError(
                    "decision-invalid", "typed transition result is invalid"
                )
            return prepared, execution

    validate_effects = staticmethod(LegacyV4Repository.validate_effects)
