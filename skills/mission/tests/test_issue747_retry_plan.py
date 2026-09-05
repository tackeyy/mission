"""#747 項目 2: retry-safe plan は callback を持たないデータ契約である.

任意 callback を受ける API では純粋性を強制できない。呼び出し側が渡すのが
データだけなら、executor は何度でも同じ結果を再現できる。
"""
import pytest


def _plan_fields(**overrides):
    fields = {
        "now": "2026-01-01T00:00:00Z",
        "iteration": 1,
        "publication_path": "build/m.json",
        "operation_id": None,
    }
    fields.update(overrides)
    return fields


def test_the_plan_carries_only_data():
    """A callback would bring back the problem this contract exists to remove."""
    import dataclasses

    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields())
    for field in dataclasses.fields(plan):
        assert not callable(getattr(plan, field.name)), field.name


def test_the_plan_is_immutable():
    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields())
    with pytest.raises(Exception):
        plan.now = "2026-01-02T00:00:00Z"


def test_the_publication_path_is_canonical_on_the_plan():
    """The path is normalised once, not on every attempt."""
    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields(publication_path="build//m.json"))
    assert plan.publication_path == "build/m.json"


def test_a_path_inside_the_repository_root_is_refused_when_the_plan_is_built():
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_application.retry_plan import ContextManifestRetryPlan

    with pytest.raises(EvidencePublicationError):
        ContextManifestRetryPlan(**_plan_fields(publication_path=".mission-state/m.json"))


def test_an_absent_iteration_is_a_meaningful_value():
    """`None` means "resolve from the snapshot", not "missing"."""
    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields(iteration=None))
    assert plan.iteration is None


def test_the_semantic_intent_is_stable_across_attempts():
    """Two plans with the same request describe the same operation."""
    from mission_application.retry_plan import ContextManifestRetryPlan

    first = ContextManifestRetryPlan(**_plan_fields())
    second = ContextManifestRetryPlan(**_plan_fields())
    assert first.semantic_intent() == second.semantic_intent()


def test_the_semantic_intent_moves_with_the_request():
    from mission_application.retry_plan import ContextManifestRetryPlan

    first = ContextManifestRetryPlan(**_plan_fields())
    other = ContextManifestRetryPlan(**_plan_fields(publication_path="build/other.json"))
    assert first.semantic_intent() != other.semantic_intent()


def test_the_operation_id_is_decided_once():
    """Without a caller id the executor must not mint a new one per attempt."""
    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields())
    assert plan.resolved_operation_id() == plan.resolved_operation_id()


def test_a_caller_operation_id_is_kept():
    from mission_application.retry_plan import ContextManifestRetryPlan

    plan = ContextManifestRetryPlan(**_plan_fields(operation_id="caller-op"))
    assert plan.resolved_operation_id() == "caller-op"
