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


@pytest.mark.parametrize(
    "broken",
    [
        {"now": None},
        {"now": 20260101},
        {"now": ""},
        {"now": "bad\x00time"},
        {"iteration": "1"},
        {"iteration": True},
        {"iteration": 0},
        {"iteration": -1},
        {"operation_id": 7},
        # A caller id is only useful if the repository can record it, so the
        # plan holds it to the same Token128 rule the record does.  Anything
        # looser is refused later as `record-invalid`, after attempts ran.
        {"operation_id": ""},
        {"operation_id": " "},
        {"operation_id": "a/b"},
        {"operation_id": "-leading-dash"},
        {"operation_id": "a" * 129},
    ],
)
def test_the_plan_refuses_what_it_cannot_carry(broken):
    """Unchecked input escaped as TypeError, which no caller handles.

    The callback route reports its refusals as `EvidenceFailure`; a plan that
    raised a bare `TypeError` would surface as a crash instead of a refusal.
    """
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_application.retry_plan import ContextManifestRetryPlan

    with pytest.raises(EvidencePublicationError):
        ContextManifestRetryPlan(**_plan_fields(**broken))


def test_the_plan_accepts_the_values_it_is_meant_to_carry():
    """Refusing everything would also pass the test above."""
    from mission_application.retry_plan import ContextManifestRetryPlan

    assert ContextManifestRetryPlan(**_plan_fields(iteration=None)).iteration is None
    assert ContextManifestRetryPlan(**_plan_fields(operation_id="op")).operation_id == "op"
    longest = "a" * 128
    assert ContextManifestRetryPlan(**_plan_fields(operation_id=longest)).operation_id == longest
    dotted = "context-manifest:0123abcd.v1_x"
    assert ContextManifestRetryPlan(**_plan_fields(operation_id=dotted)).operation_id == dotted


def test_a_refused_operation_id_carries_the_plan_code():
    """The name of the refusal is part of the contract, not only its type."""
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_application.retry_plan import ContextManifestRetryPlan

    with pytest.raises(EvidencePublicationError) as excinfo:
        ContextManifestRetryPlan(**_plan_fields(operation_id="a/b"))
    assert excinfo.value.code == "retry-plan-invalid"


def test_the_plan_and_the_kernel_share_the_timestamp_and_iteration_rules():
    """The plan must refuse exactly what `project_context_manifest` refuses.

    A second copy of either rule drifts; the plan calls the kernel's own.
    """
    import pytest as _pytest

    from mission_application.evidence_publication import EvidencePublicationError
    from mission_application.retry_plan import ContextManifestRetryPlan
    from mission_kernel.evidence import (
        EvidenceRuleError,
        context_iteration_value,
        context_timestamp_text,
    )

    for now in (None, "", "bad\x00time", 20260101):
        with _pytest.raises(EvidenceRuleError) as kernel_exc:
            context_timestamp_text(now)
        with _pytest.raises(EvidencePublicationError) as plan_exc:
            ContextManifestRetryPlan(**_plan_fields(now=now))
        assert plan_exc.value.code == kernel_exc.value.code == "timestamp-invalid"
    for iteration in ("1", 0, -1, True, 1.0):
        with _pytest.raises(EvidenceRuleError) as kernel_exc:
            context_iteration_value(iteration)
        with _pytest.raises(EvidencePublicationError) as plan_exc:
            ContextManifestRetryPlan(**_plan_fields(iteration=iteration))
        assert plan_exc.value.code == kernel_exc.value.code == "context-iteration-invalid"


def test_the_plan_and_the_record_share_one_token_rule():
    """Two copies of the rule would drift apart; the plan uses the record's."""
    from mission_kernel.identifiers import TOKEN128_RE
    from mission_persistence import fenced_commit

    assert fenced_commit._TOKEN_RE is TOKEN128_RE
