"""A2 review, score, and pass authority contracts."""

from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_corpus


@pytest.fixture(scope="module")
def scored_state(tmp_path_factory):
    from mission_kernel import decode_snapshot

    tmp_path = tmp_path_factory.mktemp("a2-scored-state")
    corpus = generate_cli_state_corpus(tmp_path.resolve())
    state = decode_snapshot(
        canonical_json_bytes(corpus["review_aggregate_and_bound_score"])
    ).state
    authoritative = replace(state.scores[-1], authoritative=True)
    return replace(state, scores=(*state.scores[:-1], authoritative))


def test_kernel_is_the_only_pass_authority_for_verified_review_score(scored_state):
    from mission_kernel.commands import MarkPass
    from mission_kernel.model import Phase, TerminalOutcome
    from mission_kernel.transitions import decide

    state = scored_state
    decision = decide(
        state,
        MarkPass(
            artifact_gate_satisfied=True,
            specialist_gate_satisfied=True,
        ),
    )

    assert decision.accepted is True
    assert decision.rule_id == "mark-pass"
    assert decision.transition is not None
    assert decision.transition.new_state.control.phase is Phase.DONE
    assert decision.transition.new_state.control.passes is True
    assert decision.transition.new_state.control.loop_active is False
    assert decision.transition.new_state.terminal_outcome is TerminalOutcome.COMPLETED_PASS
    assert decision.events[0].type == "mission-passed"
    assert decision.effects == ()


@pytest.mark.parametrize(
    ("payload_changes", "command_changes", "reason"),
    [
        ({"open_high": 1}, {}, "open-high-findings"),
        ({"composite": 3.99}, {}, "composite-below-threshold"),
        ({"min_item": 3.49}, {}, "minimum-item-below-threshold"),
        (
            {"agreement_detail": {"accuracy": {"delta": 1.51}}},
            {},
            "review-agreement-too-low",
        ),
        ({}, {"artifact_gate_satisfied": False}, "artifact-gate-unsatisfied"),
        ({}, {"specialist_gate_satisfied": False}, "specialist-gate-unsatisfied"),
    ],
    ids=[
        "open-high",
        "composite",
        "minimum-item",
        "agreement",
        "artifact",
        "specialist",
    ],
)
def test_kernel_pass_rule_rejects_each_independent_gate(
    scored_state, payload_changes, command_changes, reason
):
    from mission_kernel.commands import MarkPass
    from mission_kernel.model import FrozenJsonObject
    from mission_kernel.transitions import decide

    state = scored_state
    score = state.scores[-1]
    payload = score.payload.thaw()
    payload.update(payload_changes)
    changed_score = replace(
        score,
        payload=FrozenJsonObject(
            tuple((key, value) for key, value in payload.items())
        ),
    )
    state = replace(state, scores=(*state.scores[:-1], changed_score))
    command = MarkPass(
        **{
            "artifact_gate_satisfied": True,
            "specialist_gate_satisfied": True,
            **command_changes,
        }
    )

    decision = decide(state, command)

    assert decision.accepted is False
    assert decision.rejection.code == reason
    assert decision.transition is None
    assert decision.events == ()
    assert decision.effects == ()


def test_unverified_score_cannot_produce_pass_transition(scored_state):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide

    state = scored_state
    unverified = replace(state.scores[-1], authoritative=False)
    state = replace(state, scores=(*state.scores[:-1], unverified))

    decision = decide(
        state,
        MarkPass(artifact_gate_satisfied=True, specialist_gate_satisfied=True),
    )

    assert decision.accepted is False
    assert decision.rejection.code == "authoritative-score-required"


@pytest.mark.parametrize(
    ("approval_verified", "artifact_satisfied", "accepted", "reason"),
    [
        (False, True, False, "force-approval-required"),
        (True, False, False, "artifact-gate-unsatisfied"),
        (True, True, True, None),
    ],
    ids=["missing-pinned-approval", "artifact-still-required", "verified-force"],
)
def test_force_pass_requires_pinned_approval_and_keeps_artifact_gate(
    scored_state, approval_verified, artifact_satisfied, accepted, reason
):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide

    state = scored_state
    command = MarkPass(
        force=True,
        force_approval_verified=approval_verified,
        artifact_gate_satisfied=artifact_satisfied,
        specialist_gate_satisfied=False,
    )

    decision = decide(state, command)

    assert decision.accepted is accepted
    if accepted:
        assert decision.rule_id == "mark-pass"
    else:
        assert decision.rejection.code == reason
        assert decision.transition is None


def test_a2_commands_have_one_application_owner():
    from mission_application.review import REVIEW_COMMAND_OWNERS

    assert REVIEW_COMMAND_OWNERS == {
        "aggregate-reviews": "A2.review",
        "closeout": "A2.review",
        "manual-score-capture": "A2.review",
        "mark-passes": "A2.review",
        "push-score": "A2.review",
        "review-finalize": "A2.review",
        "review-import": "A2.review",
        "supersede-reviews": "A2.review",
    }


def test_mark_passes_cli_is_only_an_application_adapter():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_mark_passes"
    )
    calls = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    forbidden = {
        "StateLock",
        "atomic_write_json",
        "backup_state",
        "_revalidate_score_provenance",
        "_validate_findings_evidence_gate",
        "_write_terminal_outcome",
    }

    assert "run_mark_pass" in calls
    assert calls.isdisjoint(forbidden)
    assert not any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "passes"
            for target in node.targets
        )
        for node in ast.walk(function)
    )


def test_strict_adapters_yield_typed_immutable_references():
    from mission_application.review import typed_manual_score_ref, typed_review_input_ref
    from mission_kernel.model import ManualScoreRef, ReviewInputRef

    review = typed_review_input_ref(
        {
            "kind": "review-input",
            "path": ".mission-state/archive/review.json",
            "digest": "sha256:" + "a" * 64,
            "size": 128,
            "iteration": 1,
            "perspective": "correctness",
        }
    )
    manual = typed_manual_score_ref(
        {
            "kind": "manual-score",
            "path": ".mission-state/archive/manual.json",
            "digest": "sha256:" + "b" * 64,
            "generation": "b" * 16,
            "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        }
    )

    assert isinstance(review, ReviewInputRef)
    assert isinstance(manual, ManualScoreRef)
    with pytest.raises(AttributeError):
        review.digest = "sha256:" + "c" * 64


def test_review_reduction_counts_high_even_with_arbitrary_legacy_status():
    from mission_application.review import reduce_reviews_to_score
    from scoring_provenance import reduce_review_aggregate

    reviews = [
        {
            "schema": "mission-review/1",
            "iteration": 1,
            "perspective": "correctness",
            "scores": {
                "mission_achievement": 4.0,
                "accuracy": 4.0,
                "completeness": 4.0,
                "usability": 4.0,
            },
            "findings": [
                {
                    "id": "finding-1",
                    "severity": "High",
                    "axis": "accuracy",
                    "status": "resolved-by-provider-text",
                    "summary": "must remain open in the migration",
                    "recommendation": "fix the issue",
                }
            ],
            "same_score_note": "independent evidence",
        }
    ]

    reduced = reduce_reviews_to_score(
        reviews, expected_iteration=1, reducer=reduce_review_aggregate
    )

    assert reduced.open_high == 1


def _canonical_reduction_result():
    items = {
        "mission_achievement": 1.0,
        "accuracy": 1.0,
        "completeness": 1.0,
        "usability": 1.0,
    }
    return {
        "items": items,
        "composite": 1.0,
        "min_item": 1.0,
        "open_high": 0,
        "review_agreement": None,
        "agreement_detail": {
            axis: {"min": value, "max": value, "delta": 0.0}
            for axis, value in items.items()
        },
    }


def _canonical_reviews(*, scores=(1.0,), findings=None):
    return [
        {
            "schema": "mission-review/1",
            "iteration": 1,
            "perspective": f"reviewer-{index}",
            "scores": {
                "mission_achievement": score,
                "accuracy": score,
                "completeness": score,
                "usability": score,
            },
            "findings": [] if findings is None else findings,
            "same_score_note": "each axis independently checked",
        }
        for index, score in enumerate(scores, start=1)
    ]


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [("composite", 5.0), ("min_item", 5.0), ("composite", float("nan"))],
)
def test_review_reduction_rejects_forged_derived_score(field, forged_value):
    from mission_application.review import ReviewFailure, reduce_reviews_to_score

    def forged_reducer(_reviews, *, expected_iteration):
        assert expected_iteration == 1
        return {**_canonical_reduction_result(), field: forged_value}

    with pytest.raises(ReviewFailure, match="review reduction"):
        reduce_reviews_to_score(
            _canonical_reviews(), expected_iteration=1, reducer=forged_reducer
        )


@pytest.mark.parametrize(
    "mutate_detail",
    [
        "missing-axis",
        "unknown-field",
        "inconsistent-delta",
        "item-outside-range",
        "agreement-mismatch",
    ],
)
def test_review_reduction_rejects_noncanonical_agreement_detail(mutate_detail):
    from mission_application.review import ReviewFailure, reduce_reviews_to_score

    def forged_reducer(_reviews, *, expected_iteration):
        assert expected_iteration == 1
        result = _canonical_reduction_result()
        if mutate_detail == "missing-axis":
            result["agreement_detail"].pop("accuracy")
        elif mutate_detail == "unknown-field":
            result["agreement_detail"]["accuracy"]["median"] = 1.0
        elif mutate_detail == "inconsistent-delta":
            result["agreement_detail"]["accuracy"] = {
                "min": 1.0, "max": 2.0, "delta": 0.0,
            }
        elif mutate_detail == "item-outside-range":
            result["agreement_detail"]["accuracy"] = {
                "min": 2.0, "max": 2.0, "delta": 0.0,
            }
        else:
            result["review_agreement"] = 4.0
        return result

    with pytest.raises(ReviewFailure, match="review reduction"):
        reduce_reviews_to_score(
            _canonical_reviews(), expected_iteration=1, reducer=forged_reducer
        )


def test_review_reduction_rejects_forged_open_high_count():
    from mission_application.review import ReviewFailure, reduce_reviews_to_score

    finding = {
        "id": "finding-1",
        "severity": "High",
        "axis": "accuracy",
    }
    reviews = _canonical_reviews(findings=[finding])

    def forged_reducer(_reviews, *, expected_iteration):
        assert expected_iteration == 1
        return _canonical_reduction_result()

    with pytest.raises(ReviewFailure, match="review reduction"):
        reduce_reviews_to_score(reviews, expected_iteration=1, reducer=forged_reducer)


def test_review_reduction_rejects_agreement_hidden_from_input_reviews():
    from mission_application.review import ReviewFailure, reduce_reviews_to_score

    def forged_reducer(_reviews, *, expected_iteration):
        assert expected_iteration == 1
        result = _canonical_reduction_result()
        result["items"] = {axis: 3.0 for axis in result["items"]}
        result["composite"] = 3.0
        result["min_item"] = 3.0
        result["agreement_detail"] = {
            axis: {"min": 3.0, "max": 3.0, "delta": 0.0}
            for axis in result["items"]
        }
        return result

    with pytest.raises(ReviewFailure, match="does not match review inputs"):
        reduce_reviews_to_score(
            _canonical_reviews(scores=(1.0, 5.0)),
            expected_iteration=1,
            reducer=forged_reducer,
        )


def test_review_reduction_returns_canonical_axis_order():
    from mission_application.review import reduce_reviews_to_score
    from scoring_provenance import reduce_review_aggregate

    def reordered_reducer(reviews, *, expected_iteration):
        result = reduce_review_aggregate(
            reviews, expected_iteration=expected_iteration
        )
        result["items"] = dict(reversed(list(result["items"].items())))
        result["agreement_detail"] = dict(
            reversed(list(result["agreement_detail"].items()))
        )
        return result

    reduced = reduce_reviews_to_score(
        _canonical_reviews(), expected_iteration=1, reducer=reordered_reducer
    )

    assert list(reduced.items) == [
        "mission_achievement", "accuracy", "completeness", "usability",
    ]
    assert list(reduced.agreement_detail) == list(reduced.items)


@pytest.mark.parametrize(
    ("base_sha", "head_sha"),
    [(None, None), ("", ""), ("a" * 39, "b" * 40), (1, 2)],
)
def test_manual_score_ref_rejects_noncanonical_git_revision_scope(base_sha, head_sha):
    from mission_application.review import ReviewFailure, typed_manual_score_ref

    value = {
        "kind": "manual-score",
        "path": ".mission-state/archive/manual.json",
        "digest": "sha256:" + "b" * 64,
        "generation": "b" * 16,
        "revision_scope": {
            "kind": "git",
            "base_sha": base_sha,
            "head_sha": head_sha,
        },
    }

    with pytest.raises(ReviewFailure, match="revision scope"):
        typed_manual_score_ref(value)


def test_manual_capture_foreign_lease_leaves_state_and_public_files_unchanged(
    state_dir, run_cli, tmp_path
):
    import json

    from scoring_provenance import digest

    acquired = run_cli("set", "lease_probe=true", cwd=state_dir.parent)
    assert acquired.returncode == 0, acquired.stderr
    session = state_dir / "sessions" / "test.json"
    state = json.loads(session.read_text(encoding="utf-8"))
    items = {
        "mission_achievement": 4.5,
        "accuracy": 4.5,
        "completeness": 4.5,
        "usability": 4.5,
    }
    unsigned = {
        "schema": "mission-manual-score/1",
        "session_id": state["session_id"],
        "mission_id": state["mission_id"],
        "iteration": 1,
        "items": items,
        "composite": 4.5,
        "min_item": 4.5,
        "review_agreement": 4.5,
        "open_high": 0,
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {
            "kind": "manual-source-evidence",
            "ref": "sha256:" + "d" * 64,
            "digest": "sha256:" + "d" * 64,
        },
        "imported_at": "2026-08-17T00:00:00Z",
    }
    source = tmp_path / "manual.json"
    source.write_text(
        json.dumps({**unsigned, "input_digest": digest(unsigned)}), encoding="utf-8"
    )
    output = tmp_path / "scoring.json"
    before_state = session.read_bytes()
    archive = state_dir / "archive"
    before_archive = {
        path.relative_to(archive): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    } if archive.exists() else {}

    result = run_cli(
        "manual-score-capture",
        "--input",
        str(source),
        "--out",
        str(output),
        cwd=state_dir.parent,
        env_extra={"MISSION_LEASE_ID": "foreign-lease"},
    )

    after_archive = {
        path.relative_to(archive): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    } if archive.exists() else {}
    assert result.returncode == 2
    assert session.read_bytes() == before_state
    assert after_archive == before_archive
    assert not output.exists()


def test_manual_capture_rejects_duplicate_json_before_publication(
    state_dir, run_cli, tmp_path
):
    session = state_dir / "sessions" / "test.json"
    source = tmp_path / "duplicate.json"
    source.write_text('{"schema":"mission-manual-score/1","schema":"forged"}', encoding="utf-8")
    output = tmp_path / "scoring.json"
    before = session.read_bytes()

    result = run_cli(
        "manual-score-capture",
        "--input",
        str(source),
        "--out",
        str(output),
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "duplicate JSON key" in result.stderr
    assert session.read_bytes() == before
    assert not output.exists()
