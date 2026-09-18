"""Closed A4 plan, handoff, and provider-evidence application contracts.

The functions in this module deliberately accept already-captured values.  A
CLI adapter may read files and invoke a provider, but it must first commit the
typed dispatch intent through the repository and may never turn provider output
into mission authority by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import re
from typing import Callable, Mapping, Sequence

from plan_contract import parse_provider_result
from provider_receipt_contract import (
    ProviderReceiptContractError,
    validate_closed_provider_receipt as _validate_closed_provider_receipt,
    validate_fencing_epoch as _validate_fencing_epoch,
)
from mission_application.ports import (
    LegacyCommandExecutionResult,
    PreparedTransitionOperation,
)
from mission_kernel.transitions import handoff_discard_refusal
from mission_kernel.commands import (
    BeginExecutorHandoff,
    CanonicalPlanObservation,
    AbortExecutorHandoff,
    CanonicalPlanRejectionCode,
    HandoffAbortReason,
    CompleteExecutorHandoff,
    RecordExecutorStep,
    RecordSpecialistRecommendation,
    RejectExecutorHandoff,
    VerifyExecutorStep,
)
from mission_kernel.a4 import (
    A4ProjectionError,
    specialist_recommendation_projection,
)


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INVOCATION_ID = re.compile(r"inv_[0-9a-f]{32}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}\Z")
_PLAN_FIELDS = frozenset(
    {
        "schema",
        "path",
        "digest",
        "source",
        "source_id",
        "source_digest",
        "selection_source",
        "iteration",
        "generation",
        "validated_at",
    }
)
_HANDOFF_FIELDS = frozenset(
    {
        "schema",
        "handoff_id",
        "plan_path",
        "plan_digest",
        "plan_generation",
        "plan_source",
        "source_id",
        "selection_source",
        "iteration",
        "step_ids",
        "status",
    }
)
_INTENT_FIELDS = frozenset(
    {"invocation_id", "operation_id", "outbound_packet_digest", "iteration", "fencing_epoch"}
)
_HANDOFF_DECISION_FIELDS = frozenset(
    {
        "handoff_id",
        "plan_digest",
        "plan_generation",
        "plan_source",
        "source_id",
        "selection_source",
        "iteration",
        "step_id",
        "result",
    }
)


class PlanningFailure(ValueError):
    """A fail-closed A4 request rejection with a stable reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_int(value: object, *, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _relative_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\x00" not in value
        and not value.startswith("/")
        and ".." not in value.split("/")
    )


def validate_closed_provider_receipt(value: object, *, required_kind: str | None = None) -> dict:
    """Close the receipt carried between an execution adapter and public state.

    A receipt identity is opaque evidence, never a local locator.  Keeping the
    rule here makes the application decision, strict adapter, and persistence
    boundary agree on precisely the same v4 wire object.
    """
    try:
        return _validate_closed_provider_receipt(value, required_kind=required_kind)
    except ProviderReceiptContractError as exc:
        raise PlanningFailure("receipt-invalid") from exc


def validate_fencing_epoch(value: object) -> int:
    """Admit only a positive, non-boolean lease fence."""
    try:
        return _validate_fencing_epoch(value)
    except ProviderReceiptContractError as exc:
        raise PlanningFailure("dispatch-intent-invalid") from exc


def _closed_handoff_wire(value: object) -> bool:
    """Accept exactly one legacy handoff lifecycle variant."""
    if not isinstance(value, Mapping):
        return False
    extra_by_status = {
        "prepared": frozenset(),
        "consuming": frozenset({"begun_at"}),
        "consumed": frozenset({"begun_at", "consumed_at"}),
        "rejected": frozenset({"rejected_reason"}),
    }
    extras = extra_by_status.get(value.get("status"))
    return (
        extras is not None
        and set(value) == _HANDOFF_FIELDS | extras
        and all(isinstance(value.get(field), str) and bool(value[field]) for field in extras)
    )


@dataclass(frozen=True)
class PlanBinding:
    """The complete lineage the application must bind before plan use."""

    path: str
    digest: str
    source: str
    source_id: str
    source_digest: str
    selection_source: str
    invocation_id: str | None
    iteration: int
    generation: int


@dataclass(frozen=True)
class HandoffBinding:
    handoff_id: str
    plan: PlanBinding
    step_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class DispatchIntent:
    invocation_id: str
    operation_id: str
    outbound_packet_digest: str
    iteration: int
    fencing_epoch: int


@dataclass(frozen=True)
class ExecutorHandoffRequest:
    operation: str
    at: str
    step_id: str | None = None
    result: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ExecutorHandoffFacts:
    plan_path: object
    plan_digest: object
    plan_generation: object
    plan_source: object
    source_id: object
    selection_source: object
    iteration: object
    step_ids: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]
    decision_iteration: object
    raw: bytes = b""


@dataclass(frozen=True)
class ExecutorHandoffResult:
    handoff: dict
    appended_decision: dict | None


def prepare_executor_handoff(
    state: object,
    request: object,
    facts: object,
) -> PreparedTransitionOperation:
    """Prepare one typed handoff command from adapter-verified plan facts."""
    if not isinstance(state, Mapping):
        raise PlanningFailure("executor-handoff-state-invalid")
    if not isinstance(request, ExecutorHandoffRequest) or not isinstance(
        facts, ExecutorHandoffFacts
    ):
        raise PlanningFailure("executor-handoff-request-invalid")
    if request.operation == "abort":
        # #767 D3.  Abort ends the handoff and nothing else, so the plan facts
        # the adapter carries are not consulted -- including the consistency
        # check below.  Requiring them would tie the exit to a plan that, in
        # the cases abort exists for, is already gone or already replaced.
        return prepare_executor_handoff_abort(
            state, at=request.at, reason_code=request.reason
        )
    if set(facts.dependencies) != set(facts.step_ids):
        raise PlanningFailure("executor-handoff-dependencies-invalid")
    try:
        dependencies = tuple(
            (step_id, tuple(facts.dependencies[step_id]))
            for step_id in facts.step_ids
        )
    except (KeyError, TypeError) as exc:
        raise PlanningFailure("executor-handoff-dependencies-invalid") from exc
    observation = CanonicalPlanObservation(
        path=facts.plan_path,
        digest=facts.plan_digest,
        generation=facts.plan_generation,
        source=facts.plan_source,
        source_id=facts.source_id,
        selection_source=facts.selection_source,
        iteration=facts.iteration,
        ordered_step_ids=facts.step_ids,
        dependencies=dependencies,
        raw=facts.raw,
    )
    commands = {
        "begin": lambda: BeginExecutorHandoff(request.at, observation),
        "verify": lambda: VerifyExecutorStep(
            request.at, request.step_id, observation
        ),
        "record": lambda: RecordExecutorStep(
            request.at, request.step_id, request.result, observation
        ),
        "complete": lambda: CompleteExecutorHandoff(request.at, observation),
    }
    factory = commands.get(request.operation)
    if factory is None:
        raise PlanningFailure("executor-handoff-request-invalid")
    return PreparedTransitionOperation(
        command=factory(),
        effects=(),
        result={"operation": request.operation},
    )


def prepare_executor_handoff_rejection(
    state: object,
    *,
    at: object,
    attempted_operation: object,
    reason_code: object,
) -> PreparedTransitionOperation:
    """Prepare the closed begin/verify canonical-drift transition."""
    if not isinstance(state, Mapping) or not isinstance(at, str) or not at:
        raise PlanningFailure("executor-handoff-rejection-invalid")
    attempted = {
        "begin": "begin",
        "verify": "verify-step",
    }.get(attempted_operation)
    try:
        reason = CanonicalPlanRejectionCode(reason_code)
    except (TypeError, ValueError) as exc:
        raise PlanningFailure("executor-handoff-rejection-invalid") from exc
    if attempted is None:
        raise PlanningFailure("executor-handoff-rejection-invalid")
    return PreparedTransitionOperation(
        command=RejectExecutorHandoff(at, attempted, reason),
        effects=(),
        result={
            "operation": attempted_operation,
            "rejection": reason.value,
        },
    )


# --- executor handoff: the static per-operation tables ------------------------
#
# These are policy, not wiring: they decide which facts an operation is run
# with.  They live here rather than beside the argparse code so that they are
# reviewed as application logic (#767 round 1).

# Operation -> kernel command type.
EXECUTOR_HANDOFF_COMMAND_NAMES = {
    "begin": "executor-handoff-begin",
    "verify": "executor-handoff-verify-step",
    "record": "executor-handoff-record-step",
    "complete": "executor-handoff-complete",
    "abort": "executor-handoff-abort",
}

# #767 D3.  ``abort`` must not read the canonical plan.  The situations it
# exists for -- an executor that stopped answering, a plan about to be replaced
# -- are the ones where that read fails, so requiring it would shut the only
# exit at the moment it is needed.
EXECUTOR_HANDOFF_READS_PLAN = {
    "begin": True,
    "verify": True,
    "record": True,
    "complete": True,
    "abort": False,
}

# (the operation was already replayed, the operation reads the plan) -> which
# source the step ids come from.  ``replay`` derives them from the stored
# handoff and touches no file, which is also what abort needs.
EXECUTOR_HANDOFF_FACTS_SOURCE = {
    (True, True): "replay",
    (True, False): "replay",
    (False, True): "fresh",
    (False, False): "replay",
}

# The closed set of abort reasons, as the CLI must offer them.  Derived from the
# kernel enum so the two cannot drift.
EXECUTOR_HANDOFF_ABORT_REASONS = tuple(member.value for member in HandoffAbortReason)


def prepare_executor_handoff_abort(
    state: object,
    *,
    at: object,
    reason_code: object,
) -> PreparedTransitionOperation:
    """Prepare the published exit for an open handoff (#767 D3).

    The reason arrives as a plain string from the command line and is turned
    into the closed enum here.  Anything the enum does not name -- including a
    canonical-drift code -- is refused, so a person's decision never lands in
    the vocabulary that drift counts are read from.
    """
    if not isinstance(state, Mapping) or not isinstance(at, str) or not at:
        raise PlanningFailure("executor-handoff-abort-invalid")
    try:
        reason = HandoffAbortReason(reason_code)
    except (TypeError, ValueError) as exc:
        raise PlanningFailure("executor-handoff-abort-invalid") from exc
    return PreparedTransitionOperation(
        command=AbortExecutorHandoff(at, reason),
        effects=(),
        result={
            "operation": "abort",
            "abort_reason": reason.value,
        },
    )


class TransitionRejected(Exception):
    """One typed transition could not be executed; the message names why."""


class ExecutorHandoffRejected(TransitionRejected):
    """One executor handoff could not be executed; the message names why."""


@dataclass(frozen=True)
class UnpreparedOperation:
    """Stands in for a prepare that failed, until admission says what it was (#773).

    The v5 executor runs prepare before it can tell the caller whether the
    operation is a replay, so a failure against the current state arrives too
    early to act on.  This carries the transaction that far and nothing
    further: it is deliberately absent from the kernel's decision table, so
    ``decide`` answers ``unknown-command`` and nothing is committed.  The
    caller then either discards the held failure (it was a replay) or raises
    it in place of that rejection, so this type never reaches a user.
    """


def unprepared_operation() -> PreparedTransitionOperation:
    """Return the prepared operation that stands in for a failed prepare.

    The effects are empty so that the request carries the same (absent)
    materialization as a real executor handoff; a generated blob here would
    give ``_assert_replay_materializes`` something to disagree about.
    """
    return PreparedTransitionOperation(
        command=UnpreparedOperation(), effects=(), result={}
    )


# #773 D2.  A replayed operation records only its state, never its response, so
# success and failure are read back from the handoff it left behind.  The two
# vocabularies are disjoint (fixed by #767), and membership is tested rather
# than a prefix: a prefix test would silently misread a value whose spelling
# changed, and these sets are the authority on what each one means.
_REPLAY_FAILURE_REASONS = frozenset(
    member.value for member in CanonicalPlanRejectionCode
)
_REPLAY_SUCCESS_REASONS = frozenset(member.value for member in HandoffAbortReason)
UNKNOWN_REPLAY_REASON = "executor-handoff-replay-reason-unknown"


MISSING_REPLAY_HANDOFF = "executor-handoff-replay-handoff-missing"


def _replayed_handoff_failure(handoff: object) -> str | None:
    """Return the reason the replayed operation failed, or ``None`` if it did not."""
    if not isinstance(handoff, Mapping):
        # Every handoff command leaves a handoff behind, so its absence means
        # the recorded state is not the one this operation wrote.  Answering
        # ``ok`` with nothing in hand would be the same fail-open the unknown
        # reason code is closed against.
        return MISSING_REPLAY_HANDOFF
    if handoff.get("status") != "rejected":
        return None
    reason = handoff.get("rejected_reason")
    # Failure is tested first.  The two vocabularies are disjoint today (fixed by
    # a test in #767), so the order does not change any answer -- but if that
    # ever breaks, a value claimed by both should be read as the failure.
    if isinstance(reason, str) and reason in _REPLAY_FAILURE_REASONS:
        return reason
    if reason in _REPLAY_SUCCESS_REASONS:
        return None
    # Neither vocabulary claims it, so what the operation did is unknown.
    return UNKNOWN_REPLAY_REASON


def run_transition_effects(
    repository: object,
    prepare,
    *,
    passthrough: tuple,
    rejected: tuple = (OSError, ValueError),
    rejection: type = TransitionRejected,
) -> tuple:
    """Execute one typed transition through its repository port.

    Failures the adapter reports as a plain rejection are collapsed into
    ``rejection``.  ``passthrough`` names the exception types that must *not*
    be collapsed even when they are subclasses of a rejected type -- the
    fenced persistence error is a ``ValueError`` whose code the CLI
    classifies (lease codes, CAS codes, ``operation-history-collected``), so
    it has to reach that classifier rather than end as a generic rejection
    (#747 item 6).  ``passthrough`` is checked first for that reason, and it
    has no default: a caller that forgets it would rebuild the very trap this
    helper exists to remove, so an empty tuple is refused as well.
    """
    if not passthrough:
        raise ValueError("run_transition_effects requires a non-empty passthrough")
    try:
        return repository.execute_transition_effects(prepare)
    except passthrough:
        raise
    except rejected as exc:
        raise rejection(str(exc)) from exc


def run_executor_handoff(
    repository: object,
    prepare,
    *,
    operation: str,
    passthrough: tuple,
    rejected: tuple = (OSError, ValueError),
) -> dict:
    """Execute one handoff and close it into the stable CLI response.

    ``operation`` is the subcommand this invocation ran.  It is passed rather
    than read back from the state because a replayed operation's state does not
    name it -- a ``consuming`` handoff looks the same after ``begin``,
    ``verify-step`` and ``record-step``.  Taking it from the caller is sound
    because the operation is folded into the command the intent digest covers,
    so a different subcommand is a different operation, not a replay (#773).
    """
    held: list[BaseException] = []

    def prepare_or_hold(data):
        try:
            return prepare(data)
        except passthrough:
            raise
        except rejected as exc:
            # Whether the current state admits this operation only matters if
            # it is about to be committed, and that is not known until
            # admission.  Hold the failure and answer once it is.
            held.append(exc)
            return unprepared_operation()

    prepared, execution = run_transition_effects(
        repository, prepare_or_hold, passthrough=passthrough, rejected=rejected,
        rejection=ExecutorHandoffRejected,
    )
    if held and not execution.replayed:
        # Not a replay: the failure stands.  It is raised in place of the
        # placeholder's ``unknown-command`` rejection, which says nothing about
        # why the operation could not be prepared.
        raise ExecutorHandoffRejected(str(held[0])) from held[0]
    try:
        return executor_handoff_response(prepared, execution, operation=operation)
    except passthrough:
        raise
    except rejected as exc:
        raise ExecutorHandoffRejected(str(exc)) from exc


def executor_handoff_response(
    prepared: object,
    execution: object,
    *,
    operation: object,
) -> dict:
    """Close one executor result into the stable CLI response or rejection.

    On a replay nothing this transaction's prepare produced is read: it was
    built against the current state, which the replayed operation never saw and
    which nothing here is going to change.  The answer is the state that
    operation committed (#773).
    """
    if not isinstance(prepared, PreparedTransitionOperation) or not isinstance(
        execution, LegacyCommandExecutionResult
    ):
        raise PlanningFailure("executor-handoff-execution-invalid")
    if operation not in {"begin", "verify", "record", "complete", "abort"}:
        raise PlanningFailure("executor-handoff-execution-invalid")
    decision = execution.decision
    if decision is not None and not decision.accepted:
        reason = decision.rejection
        raise PlanningFailure(
            reason.code
            if reason is not None
            else "executor-handoff-transition-rejected"
        )
    if execution.replayed:
        replayed_state = execution.replayed_state
        if replayed_state is None:
            # Unreachable today: ``LegacyCommandExecutionResult`` refuses to be
            # built with ``replayed`` set and no state.  Kept because reading a
            # replay out of the current head is the defect this function exists
            # to fix, and a loosened invariant must not restore it silently.
            raise PlanningFailure("executor-handoff-execution-invalid")
        handoff = replayed_state.thaw().get("executor_handoff")
        failure = _replayed_handoff_failure(handoff)
        if failure is not None:
            raise PlanningFailure(failure)
        return {"ok": True, "operation": operation, "executor_handoff": handoff}
    handoff = execution.projection.get("executor_handoff")
    if operation != "abort":
        # A rejected handoff means the canonical plan drifted, which is a
        # failure to report.  For abort, ``rejected`` is the outcome the caller
        # asked for, so it is not one (#767).
        rejection = prepared.result.get("rejection")
        if isinstance(rejection, str):
            raise PlanningFailure(rejection)
    return {
        "ok": True,
        "operation": operation,
        "executor_handoff": handoff,
    }


def prepare_specialist_recommendation(
    state: object,
    *,
    at: object,
    expected_complexity: object,
    expected_iteration: object,
    result: object,
) -> PreparedTransitionOperation:
    """Prepare a selection-only checkpoint for the shared typed executor."""
    if (
        not isinstance(state, Mapping)
        or not isinstance(at, str)
        or not at
        or (
            expected_complexity is not None
            and not isinstance(expected_complexity, str)
        )
        or type(expected_iteration) is not int
        or expected_iteration < 0
    ):
        raise PlanningFailure("specialist-recommendation-invalid")
    try:
        projection = specialist_recommendation_projection(result)
    except A4ProjectionError as exc:
        raise PlanningFailure(exc.code) from exc
    return PreparedTransitionOperation(
        command=RecordSpecialistRecommendation(
            at,
            expected_complexity,
            expected_iteration,
            projection,
        ),
        effects=(),
        result={},
    )


def typed_plan_binding(value: object) -> PlanBinding:
    """Close a legacy wire plan before it reaches any A4 mutation.

    The provider invocation is derived only from the state-owned provider plan
    source ID.  Core plans intentionally have no invocation identity.
    """
    if not isinstance(value, Mapping) or set(value) != _PLAN_FIELDS:
        raise PlanningFailure("plan-binding-invalid")
    source = value.get("source")
    source_id = value.get("source_id")
    valid = (
        value.get("schema") == "mission-plan/1"
        and _relative_path(value.get("path"))
        and _is_digest(value.get("digest"))
        and source in {"core", "provider"}
        and isinstance(source_id, str)
        and _IDENTIFIER.fullmatch(source_id) is not None
        and _is_digest(value.get("source_digest"))
        and isinstance(value.get("selection_source"), str)
        and bool(value.get("selection_source"))
        and _is_int(value.get("iteration"), minimum=0)
        and _is_int(value.get("generation"), minimum=1)
        and isinstance(value.get("validated_at"), str)
        and bool(value.get("validated_at"))
    )
    if not valid or (source == "provider" and _INVOCATION_ID.fullmatch(source_id) is None):
        raise PlanningFailure("plan-binding-invalid")
    return PlanBinding(
        path=value["path"],
        digest=value["digest"],
        source=source,
        source_id=source_id,
        source_digest=value["source_digest"],
        selection_source=value["selection_source"],
        invocation_id=source_id if source == "provider" else None,
        iteration=value["iteration"],
        generation=value["generation"],
    )


def typed_handoff(value: object, plan: object) -> HandoffBinding:
    """Close and bind a prepared legacy handoff to the current typed plan."""
    bound_plan = typed_plan_binding(plan)
    if not _closed_handoff_wire(value):
        raise PlanningFailure("handoff-binding-invalid")
    steps = value.get("step_ids")
    valid = (
        value.get("schema") == "mission-executor-handoff/1"
        and isinstance(value.get("handoff_id"), str)
        and _IDENTIFIER.fullmatch(value["handoff_id"]) is not None
        and isinstance(steps, list)
        and bool(steps)
        and all(isinstance(step, str) and _IDENTIFIER.fullmatch(step) is not None for step in steps)
        and len(steps) == len(set(steps))
        and value.get("status") in {"prepared", "consuming", "consumed", "rejected"}
        and _is_int(value.get("iteration"), minimum=0)
        and _is_int(value.get("plan_generation"), minimum=1)
    )
    if not valid:
        raise PlanningFailure("handoff-binding-invalid")
    if (
        value["plan_path"] != bound_plan.path
        or value["plan_digest"] != bound_plan.digest
        or value["plan_generation"] != bound_plan.generation
        or value["plan_source"] != bound_plan.source
        or value["source_id"] != bound_plan.source_id
        or value["selection_source"] != bound_plan.selection_source
        or value["iteration"] != bound_plan.iteration
    ):
        raise PlanningFailure("handoff-plan-drift")
    return HandoffBinding(value["handoff_id"], bound_plan, tuple(steps), value["status"])


def verify_handoff_binding(
    value: object,
    *,
    plan_path: object,
    plan_digest: object,
    plan_generation: object,
    plan_source: object,
    source_id: object,
    selection_source: object,
    iteration: object,
    step_ids: object,
) -> tuple[str, ...]:
    """Bind a v4 executor handoff to adapter-verified plan authority facts.

    The adapter first re-reads the canonical document and its state-owned
    lineage.  This use case then closes the handoff's wire object before any
    begin, step, or complete mutation.  It deliberately accepts the older v4
    plan representation rather than inventing missing typed fields.
    """
    if not _closed_handoff_wire(value):
        raise PlanningFailure("handoff-binding-invalid")
    steps = value.get("step_ids")
    expected = (plan_path, plan_digest, plan_generation, plan_source, source_id, selection_source, iteration)
    actual = (
        value.get("plan_path"), value.get("plan_digest"), value.get("plan_generation"),
        value.get("plan_source"), value.get("source_id"), value.get("selection_source"), value.get("iteration"),
    )
    if (
        not isinstance(steps, list)
        or not steps
        or any(not isinstance(step, str) or _IDENTIFIER.fullmatch(step) is None for step in steps)
        or len(steps) != len(set(steps))
        or not isinstance(value.get("handoff_id"), str)
        or _IDENTIFIER.fullmatch(value["handoff_id"]) is None
        or value.get("schema") != "mission-executor-handoff/1"
        or value.get("status") not in {"prepared", "consuming", "consumed", "rejected"}
        or any(type(number) is not int for number in (
            plan_generation, iteration, value.get("plan_generation"), value.get("iteration")
        ))
    ):
        raise PlanningFailure("handoff-binding-invalid")
    if actual != expected or steps != step_ids:
        raise PlanningFailure("handoff-plan-drift")
    return tuple(steps)


def decide_executor_handoff(
    handoff: object,
    decisions: object,
    request: object,
    facts: object,
) -> ExecutorHandoffResult:
    """Return the sole permitted v4 handoff mutation without performing I/O."""
    if not isinstance(request, ExecutorHandoffRequest) or not isinstance(facts, ExecutorHandoffFacts):
        raise PlanningFailure("executor-handoff-request-invalid")
    if request.operation not in {"begin", "verify", "record", "complete"} or not isinstance(request.at, str) or not request.at:
        raise PlanningFailure("executor-handoff-request-invalid")
    if not isinstance(decisions, list):
        raise PlanningFailure("executor-handoff-decisions-invalid")
    steps = verify_handoff_binding(
        handoff,
        plan_path=facts.plan_path, plan_digest=facts.plan_digest,
        plan_generation=facts.plan_generation, plan_source=facts.plan_source,
        source_id=facts.source_id, selection_source=facts.selection_source,
        iteration=facts.iteration, step_ids=list(facts.step_ids),
    )
    if not isinstance(facts.dependencies, Mapping) or set(facts.dependencies) != set(steps):
        raise PlanningFailure("executor-handoff-dependencies-invalid")
    if type(facts.decision_iteration) is not int or facts.decision_iteration < 0:
        raise PlanningFailure("executor-handoff-decisions-invalid")
    if any(
        not isinstance(deps, tuple) or any(dep not in steps for dep in deps)
        for deps in facts.dependencies.values()
    ):
        raise PlanningFailure("executor-handoff-dependencies-invalid")
    next_handoff = copy.deepcopy(dict(handoff))
    matching = [
        item for item in decisions
        if isinstance(item, Mapping) and item.get("handoff_id") == next_handoff["handoff_id"]
    ]
    if any(
        set(item) != _HANDOFF_DECISION_FIELDS
        or type(item.get("plan_generation")) is not int
        or type(item.get("iteration")) is not int
        or item.get("plan_digest") != facts.plan_digest
        or item.get("plan_generation") != facts.plan_generation
        or item.get("plan_source") != facts.plan_source
        or item.get("source_id") != facts.source_id
        or item.get("selection_source") != facts.selection_source
        or item.get("iteration") != facts.decision_iteration
        or item.get("step_id") not in steps
        or item.get("result") not in {"ok", "partial", "failed"}
        for item in matching
    ):
        raise PlanningFailure("executor-handoff-decisions-invalid")
    done_steps = [item["step_id"] for item in matching]
    if len(done_steps) != len(set(done_steps)):
        raise PlanningFailure("executor-handoff-decisions-invalid")
    done = set(done_steps)
    if request.operation == "begin":
        if next_handoff["status"] != "prepared":
            raise PlanningFailure("executor-handoff-not-prepared")
        next_handoff["status"] = "consuming"
        next_handoff["begun_at"] = request.at
        return ExecutorHandoffResult(next_handoff, None)
    if request.operation == "verify":
        if next_handoff["status"] not in {"prepared", "consuming"}:
            raise PlanningFailure("executor-handoff-not-active")
        if not isinstance(request.step_id, str) or request.step_id not in steps:
            raise PlanningFailure("executor-step-not-member")
        return ExecutorHandoffResult(next_handoff, None)
    if request.operation == "record":
        if next_handoff["status"] not in {"prepared", "consuming"}:
            raise PlanningFailure("executor-handoff-not-active")
        if not isinstance(request.step_id, str) or request.step_id not in steps:
            raise PlanningFailure("executor-step-not-member")
        if request.step_id in done:
            raise PlanningFailure("executor-step-already-recorded")
        if any(dep not in done for dep in facts.dependencies[request.step_id]):
            raise PlanningFailure("executor-step-dependency-incomplete")
        if request.result not in {"ok", "partial", "failed"}:
            raise PlanningFailure("executor-step-result-invalid")
        decision = {
            "handoff_id": next_handoff["handoff_id"], "plan_digest": facts.plan_digest,
            "plan_generation": facts.plan_generation, "plan_source": facts.plan_source,
            "source_id": facts.source_id, "selection_source": facts.selection_source,
            "iteration": facts.decision_iteration, "step_id": request.step_id,
            "result": request.result,
        }
        return ExecutorHandoffResult(next_handoff, decision)
    if next_handoff["status"] != "consuming":
        raise PlanningFailure("executor-handoff-not-consuming")
    if set(steps) != done:
        raise PlanningFailure("executor-handoff-steps-incomplete")
    next_handoff["status"] = "consumed"
    next_handoff["consumed_at"] = request.at
    return ExecutorHandoffResult(next_handoff, None)


# #767 D5.  The guidance names only commands that exist.  The message this
# replaced pointed at `handoff resume`, which was never a subcommand, so the
# reader had nothing to run -- that was half of what this issue was about.
_HANDOFF_REFUSAL_GUIDANCE = {
    "handoff-in-flight": (
        "executor handoff is in flight; finish the remaining steps with "
        "`mission-state.py executor-handoff complete`, or end it with "
        "`mission-state.py executor-handoff abort --reason <code>`"
    ),
    "handoff-has-recorded-steps": (
        "executor handoff already records completed steps; finish it with "
        "`mission-state.py executor-handoff complete`, or end it with "
        "`mission-state.py executor-handoff abort --reason <code>` "
        "(replacing it would lose those steps)"
    ),
    "handoff-status-unknown": (
        "executor handoff carries a status this version does not recognise; "
        "end it with `mission-state.py executor-handoff abort --reason <code>`"
    ),
    "handoff-decisions-unknown": (
        "executor handoff decisions cannot be counted; end it with "
        "`mission-state.py executor-handoff abort --reason <code>`"
    ),
}


def handoff_refusal_guidance(refusal: object) -> str:
    """Return the sentence for one refusal code.

    A code with no entry still has to say something the reader can run, so it
    falls back to the abort route rather than to a message naming no command.
    """
    return _HANDOFF_REFUSAL_GUIDANCE.get(
        refusal, _HANDOFF_REFUSAL_GUIDANCE["handoff-status-unknown"]
    )


def raw_handoff_is_absent(handoff: object) -> bool:
    """Say whether the raw document carries no handoff at all.

    The same three spellings the codec reads as absent: the key missing, an
    explicit ``None``, and an empty object (``codec_v4._decode_legacy_handoff``).
    Reading any of them as a present-but-unreadable handoff would refuse a state
    the kernel admits, so the two layers have to spell this the same way.
    """
    return handoff is None or handoff == {}


def recorded_handoff_steps(state: Mapping, handoff: object) -> object:
    """Count the decisions already recorded against this handoff.

    A non-int is returned when the document cannot be counted, so the shared
    table refuses rather than reading an unreadable document as "no steps".

    A *missing* ``decisions`` key is not uncountable: the codec reads it as an
    empty list, so treating it as unknown here would refuse a state the kernel
    admits.  Only a present-but-not-a-list value is uncountable, and the codec
    rejects that before any command runs.
    """
    if not isinstance(state, Mapping) or not isinstance(handoff, Mapping):
        return None
    handoff_id = handoff.get("handoff_id")
    decisions = state.get("decisions", [])
    if not isinstance(handoff_id, str) or not isinstance(decisions, list):
        return None
    return sum(
        1
        for item in decisions
        if isinstance(item, Mapping) and item.get("handoff_id") == handoff_id
    )


def plan_adoption_handoff_refusal(state: Mapping, plan: object) -> str | None:
    """Return the code refusing to adopt this plan over the existing handoff.

    #767 D1.  A handoff is bound to the plan it was prepared from, so adopting a
    different one leaves a document the codec refuses to read.  Either the
    handoff goes with the plan it belonged to, or the adoption does not happen;
    writing the plan and keeping the handoff breaks the binding invariant.

    ``None`` is returned when there is nothing to refuse -- no handoff, a
    handoff the shared table admits dropping, or an adoption that leaves the
    binding untouched.  The last case is what makes applying the same adoption
    twice a no-op rather than a refusal.
    """
    handoff = state.get("executor_handoff")
    if raw_handoff_is_absent(handoff):
        return None
    if not isinstance(handoff, Mapping):
        return "handoff-status-unknown"
    if not _plan_binding_changes(state, plan):
        return None
    return handoff_discard_refusal(
        handoff.get("status"), recorded_handoff_steps(state, handoff)
    )


def _plan_binding_changes(state: Mapping, plan: object) -> bool:
    """Say whether adopting ``plan`` moves the binding the handoff is tied to.

    Compared field by field rather than by digest: re-adopting identical
    content still raises the generation, so equal digests do not mean an
    unchanged binding.  An unreadable current plan counts as a change, since
    nothing can be shown to have stayed the same.
    """
    current = state.get("canonical_plan")
    if not isinstance(current, Mapping) or not isinstance(plan, Mapping):
        return True
    return any(
        current.get(field) != plan.get(field)
        for field in (
            "path",
            "digest",
            "generation",
            "source",
            "source_id",
            "selection_source",
            "iteration",
        )
    )


def commit_plan_evidence(
    *,
    state: dict,
    plan: object,
    lease_verified: bool,
    publish: Callable[[PlanBinding], None],
) -> PlanBinding:
    """Publish only after the adapter has admitted the session lease.

    State mutation happens strictly after publication.  Thus an adapter may use
    its existing rollback transaction for publication faults without exposing
    a candidate plan as mission authority on a failed write.
    """
    binding = typed_plan_binding(plan)
    if lease_verified is not True:
        raise PlanningFailure("lease-rejected")
    refusal = plan_adoption_handoff_refusal(state, plan)
    if refusal is not None:
        # Code first, sentence after.  The adapter maps this failure the same
        # way it maps a malformed candidate, so whatever arrives here is what it
        # prints and records as the reason.  A sentence alone would drop the
        # machine-readable code from that record; a code alone would leave the
        # reader with no command to run.
        raise PlanningFailure(
            "{}: {}".format(refusal, handoff_refusal_guidance(refusal))
        )
    try:
        publish(binding)
    except Exception as exc:
        raise PlanningFailure("plan-publication-failed") from exc
    discard = _plan_binding_changes(state, plan) and not raw_handoff_is_absent(
        state.get("executor_handoff")
    )
    state["canonical_plan"] = dict(plan)
    if discard:
        # #767 D1.  The handoff belonged to the plan just replaced, and the
        # table above already said it may go.  Removing it is what lets the
        # next iteration prepare a fresh one.
        state.pop("executor_handoff", None)
    return binding


def validate_provider_plan_import(
    raw: bytes, *, expected_binding: Mapping[str, object], result_contract: Mapping[str, object],
    workspace: object,
) -> dict:
    """Invoke the strict result parser as an A4 authority boundary.

    The CLI only captures bounded bytes and publishes adapter-owned files; it
    cannot substitute a provider result, phase, review, score, or pass value.
    """
    if not isinstance(raw, bytes) or not isinstance(expected_binding, Mapping) or not isinstance(result_contract, Mapping):
        raise PlanningFailure("provider-plan-import-invalid")
    try:
        parsed = parse_provider_result(
            raw,
            expected_binding=dict(expected_binding),
            result_contract=dict(result_contract),
            workspace=workspace,
        )
    except Exception as exc:
        raise PlanningFailure("provider-plan-import-invalid") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"document", "raw_result_digest"}:
        raise PlanningFailure("provider-plan-import-invalid")
    if not isinstance(parsed["document"], dict) or not _is_digest(parsed["raw_result_digest"]):
        raise PlanningFailure("provider-plan-import-invalid")
    return parsed


def _typed_intent(value: object) -> DispatchIntent:
    if not isinstance(value, Mapping) or set(value) != _INTENT_FIELDS:
        raise PlanningFailure("dispatch-intent-invalid")
    invocation_id = value.get("invocation_id")
    operation_id = value.get("operation_id")
    if (
        not isinstance(invocation_id, str)
        or _INVOCATION_ID.fullmatch(invocation_id) is None
        or not isinstance(operation_id, str)
        or _IDENTIFIER.fullmatch(operation_id) is None
        or not _is_digest(value.get("outbound_packet_digest"))
        or not _is_int(value.get("iteration"), minimum=0)
    ):
        raise PlanningFailure("dispatch-intent-invalid")
    try:
        fencing_epoch = validate_fencing_epoch(value.get("fencing_epoch"))
    except PlanningFailure as exc:
        raise PlanningFailure("dispatch-intent-invalid") from exc
    return DispatchIntent(
        invocation_id,
        operation_id,
        value["outbound_packet_digest"],
        value["iteration"],
        fencing_epoch,
    )


def _same_intent(record: Mapping[str, object], intent: DispatchIntent) -> bool:
    return all(
        record.get(key) == value
        for key, value in (
            ("invocation_id", intent.invocation_id),
            ("operation_id", intent.operation_id),
            ("outbound_packet_digest", intent.outbound_packet_digest),
            ("iteration", intent.iteration),
            ("fencing_epoch", intent.fencing_epoch),
        )
    )


def provider_saga_state(invocations: Sequence[Mapping[str, object]], invocation_id: str) -> dict | None:
    """Return exactly one state-owned saga record; ambiguity fails closed."""
    if not isinstance(invocation_id, str) or _INVOCATION_ID.fullmatch(invocation_id) is None:
        raise PlanningFailure("invocation-id-invalid")
    matches = [dict(item) for item in invocations if isinstance(item, Mapping) and item.get("invocation_id") == invocation_id]
    if len(matches) > 1:
        raise PlanningFailure("invocation-identity-ambiguous")
    return matches[0] if matches else None


def record_dispatch_intent(invocations: Sequence[Mapping[str, object]], value: object) -> dict:
    """Create the durable pre-spawn saga state, never a running claim."""
    intent = _typed_intent(value)
    existing = provider_saga_state(invocations, intent.invocation_id)
    if existing is not None:
        raise PlanningFailure("dispatch-replay" if _same_intent(existing, intent) else "invocation-id-reused")
    return {
        "invocation_id": intent.invocation_id,
        "operation_id": intent.operation_id,
        "outbound_packet_digest": intent.outbound_packet_digest,
        "iteration": intent.iteration,
        "fencing_epoch": intent.fencing_epoch,
        "status": "dispatch-unknown",
        "lifecycle_state": "dispatch-unknown",
    }


def _receipt(value: object) -> dict:
    return validate_closed_provider_receipt(value)


def record_provider_receipt(
    invocations: Sequence[Mapping[str, object]], value: object, receipt: object
) -> dict:
    """Move a dispatch-unknown invocation to running only with its receipt."""
    intent = _typed_intent(value)
    existing = provider_saga_state(invocations, intent.invocation_id)
    if existing is None:
        raise PlanningFailure("receipt-intent-mismatch")
    if existing.get("fencing_epoch") != intent.fencing_epoch:
        raise PlanningFailure("stale-fencing-epoch")
    if not _same_intent(existing, intent):
        raise PlanningFailure("receipt-intent-mismatch")
    if existing.get("status") != "dispatch-unknown":
        raise PlanningFailure("receipt-replay")
    result = copy.deepcopy(existing)
    result["provider_receipt"] = _receipt(receipt)
    result["status"] = "running"
    result["lifecycle_state"] = "running"
    return result


def reconcile_dispatch_unknown(
    invocations: Sequence[Mapping[str, object]], value: object, *, observed_receipt: object | None
) -> dict:
    """Fence receipt-less recovery to abandonment; automatic redispatch is absent."""
    intent = _typed_intent(value)
    existing = provider_saga_state(invocations, intent.invocation_id)
    if existing is None:
        raise PlanningFailure("reconcile-intent-mismatch")
    if existing.get("fencing_epoch") != intent.fencing_epoch:
        raise PlanningFailure("stale-fencing-epoch")
    if not _same_intent(existing, intent):
        raise PlanningFailure("reconcile-intent-mismatch")
    if existing.get("status") != "dispatch-unknown":
        raise PlanningFailure("reconcile-not-dispatch-unknown")
    if observed_receipt is not None:
        raise PlanningFailure("receipt-required")
    result = copy.deepcopy(existing)
    result["status"] = "abandoned-unknown"
    result["lifecycle_state"] = "terminal"
    result["redispatch"] = False
    return result


def classify_provider_result(*, exit_code: object, result_validated: bool) -> str:
    """Provider exit status is evidence only, never a review/score/pass decision."""
    if type(exit_code) is not int or exit_code < 0 or type(result_validated) is not bool:
        raise PlanningFailure("provider-result-invalid")
    if exit_code == 0 and not result_validated:
        return "unvalidated-evidence"
    return "completed" if exit_code == 0 else "failed"


@dataclass(frozen=True)
class ProviderTerminalDecision:
    """The only terminal status class an execution adapter may persist."""

    status: str
    reason: str | None


def decide_provider_terminal_result(
    *, exit_code: object, evidence_status: object, reason: object = None
) -> ProviderTerminalDecision:
    """Turn adapter-observed evidence into a closed v4 terminal status.

    The adapter may parse its own textual evidence, but cannot promote it to a
    phase/review/score/pass decision or invent a terminal state.
    """
    if type(exit_code) is not int or exit_code < 0:
        raise PlanningFailure("provider-result-invalid")
    if evidence_status not in {"completed", "unvalidated-evidence", "prepared", "awaiting-input", "failed"}:
        raise PlanningFailure("provider-result-invalid")
    if reason is not None and not isinstance(reason, str):
        raise PlanningFailure("provider-result-invalid")
    if evidence_status == "awaiting-input":
        return ProviderTerminalDecision("awaiting-input", reason)
    if exit_code != 0:
        if evidence_status != "failed":
            raise PlanningFailure("provider-result-invalid")
        return ProviderTerminalDecision("failed", reason or f"command provider exited with status {exit_code}")
    if evidence_status == "failed":
        raise PlanningFailure("provider-result-invalid")
    if evidence_status == "unvalidated-evidence":
        return ProviderTerminalDecision("unvalidated-evidence", reason)
    if evidence_status == "prepared":
        return ProviderTerminalDecision(evidence_status, reason)
    return ProviderTerminalDecision("completed", reason)
