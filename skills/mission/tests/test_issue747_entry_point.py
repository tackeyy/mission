"""#747 項目 2: plan を受け取る entry point と、その配線.

部品が正しくても呼ばれなければ意味がない。第 1 段では「関数はあるが本番から
呼ばれていない」型の欠陥を 2 度指摘されたため、配線そのものを押さえる。
"""
import pytest


def _plan(publication_path="build/m.json"):
    from mission_application.retry_plan import ContextManifestRetryPlan

    return ContextManifestRetryPlan(
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path=publication_path,
    )


def _repository(tmp_path, *, effect_transaction=None):
    import contextlib

    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    if effect_transaction is None:

        @contextlib.contextmanager
        def effect_transaction(effects, prepared):
            yield effects

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    return V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        effect_transaction=effect_transaction,
    )


def test_the_entry_point_refuses_a_plan_it_does_not_know(tmp_path):
    """Only the plan types this stage supports may be retried."""
    from mission_application.evidence_publication import EvidencePublicationError

    repository = _repository(tmp_path)
    with pytest.raises(EvidencePublicationError) as excinfo:
        repository.execute_retry_safe_evidence_plan(object())
    assert "plan" in excinfo.value.detail


def test_the_entry_point_refuses_a_callback(tmp_path):
    """A callable is exactly what this contract exists to keep out."""
    from mission_application.evidence_publication import EvidencePublicationError

    repository = _repository(tmp_path)
    with pytest.raises(EvidencePublicationError):
        repository.execute_retry_safe_evidence_plan(lambda state: None)


def test_the_entry_point_runs_the_plan(tmp_path):
    """The happy path still publishes."""
    repository = _repository(tmp_path)
    _prepared, execution = repository.execute_retry_safe_evidence_plan(_plan())
    assert execution.decision is None or execution.decision.accepted


def test_the_existing_callback_api_is_untouched(tmp_path):
    """The single-shot API stays, or every other evidence route regresses."""
    from mission_application.evidence import prepare_progress_update

    repository = _repository(tmp_path)
    _prepared, execution = repository.execute_evidence_transition_effects(
        lambda state: prepare_progress_update(
            state,
            now="2026-01-01T00:00:00Z",
            total=1,
            completed=0,
            batch_size=1,
            last_unit=None,
            artifact_path=None,
            iteration=1,
            evidence_path="progress.json",
        )
    )
    assert execution.decision is None or execution.decision.accepted


def test_the_entry_point_uses_the_shared_retry_loop():
    """A second copy of the budget is how the two would drift apart."""
    from pathlib import Path

    import mission_persistence.legacy_v4 as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "run_with_base_retry(" in source
    assert "MAX_BASE_RETRIES" not in source, "the budget belongs to one module"


def test_the_plan_does_not_leak_its_identity_into_the_repository(tmp_path):
    """The plan's operation id belongs to the plan, not to the repository.

    Writing it onto the instance left it there afterwards, so the next
    operation on the same repository inherited it and collided.
    """
    from mission_application.evidence import prepare_progress_update

    repository = _repository(tmp_path)
    repository.execute_retry_safe_evidence_plan(_plan())

    _prepared, execution = repository.execute_evidence_transition_effects(
        lambda state: prepare_progress_update(
            state,
            now="2026-01-01T00:00:01Z",
            total=1,
            completed=0,
            batch_size=1,
            last_unit=None,
            artifact_path=None,
            iteration=1,
            evidence_path="progress.json",
        )
    )
    assert execution.decision is None or execution.decision.accepted, execution.decision


def test_an_exhausted_budget_reaches_the_cli_as_a_rejection(tmp_path):
    """Exhaustion is a refusal, not an internal error.

    `EvidencePublicationError` never reaches the CLI's controlled exit, so a
    caller saw a crash instead of "the base kept moving".
    """
    from mission_application.artifact import EvidenceFailure
    from mission_persistence.fenced_commit import (
        PRECONDITION_CAS_CODE,
        FencedCommitError,
    )

    repository = _repository(tmp_path)
    original = repository._repository.begin

    def _always_moves(request):
        raise FencedCommitError(PRECONDITION_CAS_CODE, "base moved")

    repository._repository.begin = _always_moves
    try:
        with pytest.raises(EvidenceFailure) as excinfo:
            repository.execute_retry_safe_evidence_plan(_plan())
    finally:
        repository._repository.begin = original
    assert excinfo.value.code == "base-retry-exhausted"


def test_a_caller_stable_operation_id_on_the_repository_is_kept(tmp_path):
    """The callback route honoured an id the caller configured; so must the plan route.

    `_request` used `self._operation_id or <fresh>`, which is what let a caller
    replay the same operation after a crash.  Overwriting that id with the
    plan's own minted one turned every such retry into a new operation.
    """
    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_kernel.json_codec import decode_json_object
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    seen = []
    original_begin = local.begin

    def _begin(request):
        seen.append(request.operation_id)
        return original_begin(request)

    local.begin = _begin
    repository = V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        operation_id="caller-stable",
        operation_command=decode_json_object(
            b'{"schema":"mission-command-intent/1","type":"compatibility-mutation"}'
        ),
        operation_command_type="compatibility-mutation",
    )

    repository.execute_retry_safe_evidence_plan(_plan())

    assert seen == ["caller-stable"], seen
    # The configured id is the repository's; the plan route must leave it in place.
    assert repository._operation_id == "caller-stable"


def test_an_explicit_plan_operation_id_wins_over_the_repository_default(tmp_path):
    """Plan-level identity is the more specific claim; the repository id is the fallback."""
    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_application.retry_plan import ContextManifestRetryPlan
    from mission_kernel.json_codec import decode_json_object
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    seen = []
    original_begin = local.begin

    def _begin(request):
        seen.append(request.operation_id)
        return original_begin(request)

    local.begin = _begin
    repository = V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        operation_id="caller-stable",
        operation_command=decode_json_object(
            b'{"schema":"mission-command-intent/1","type":"compatibility-mutation"}'
        ),
        operation_command_type="compatibility-mutation",
    )
    plan = ContextManifestRetryPlan(
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path="build/m.json",
        operation_id="plan-explicit",
    )

    repository.execute_retry_safe_evidence_plan(plan)

    assert seen == ["plan-explicit"], seen
    assert repository._operation_id == "caller-stable"
