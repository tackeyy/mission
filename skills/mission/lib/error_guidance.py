"""Generate bounded HINT lines for invalid-input and expected-gate failures."""

from __future__ import annotations

from pathlib import Path


def _phase_activity_example(phase: str | None) -> str:
    phase_value = (phase or "executing").strip() or "executing"
    if phase_value == "planning":
        return "mission-state.py advance --phase executing --activity active:implementation"
    if phase_value == "executing":
        return "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response"
    return f"mission-state.py advance --phase {phase_value} --activity active:implementation"


def _review_finalize_example(iteration: object | None, input_ref: object | None, min_reviewers: object | None) -> str:
    iter_value = iteration if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration > 0 else 1
    ref_value = str(input_ref or "<review_evidence_ref.path>")
    reviewers = min_reviewers if isinstance(min_reviewers, int) and not isinstance(min_reviewers, bool) and min_reviewers > 0 else 2
    return (
        "mission-state.py review-finalize "
        f"--iteration {iter_value} --input-ref {ref_value} --min-reviewers {reviewers}"
    )


def _review_import_example(iteration: object | None) -> str:
    iter_value = iteration if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration > 0 else 1
    return f"mission-state.py review-import --iteration {iter_value} --stdin < review.json"


def _review_import_keys() -> str:
    return "schema, iteration, perspective, scores, findings"


def _advance_producing_examples() -> list[str]:
    return [
        "HINT: 生成しない場合は mission-state.py advance --phase reviewing --artifact-applicability not-applicable を使ってください。",
        (
            "HINT: 生成する場合は "
            "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response "
            "--artifact-applicability producing --artifact-path <repo-relative-path> --producer-run-id <run-id> "
            "を使ってください。"
        ),
    ]


def build_guidance(command: str, reason: str, context: dict) -> list[str]:
    """Return 1-3 HINT lines for the supplied command failure."""
    command_name = str(command or "").strip()
    reason_code = str(reason or "").strip()
    context = context if isinstance(context, dict) else {}

    if command_name == "advance" and reason_code == "terminal-phase":
        phase = context.get("phase")
        return [
            f"HINT: 現 phase={phase or '<phase>'} の terminal 化は advance ではなく mark-passes / mark-halt を使ってください。",
            "HINT: 正しい呼び出し例: mission-state.py mark-passes",
        ]
    if command_name == "advance" and reason_code == "activity-format":
        phase = context.get("phase") or "executing"
        return [
            "HINT: --activity は <kind>:<reason> 形式で指定してください。",
            f"HINT: 正しい呼び出し例: {_phase_activity_example(str(phase))}",
        ]
    if command_name == "advance" and reason_code == "missing-canonical-plan":
        phase = context.get("phase") or "executing"
        if (
            context.get("planning_strategy") in {None, "core"}
            and context.get("planning_provider_required") is not True
        ):
            return [
                f"HINT: policy v1 の {phase} では先に canonical plan を登録してから advance を呼んでください。",
                "HINT: 正しい呼び出し例: mission-state.py planning adopt-core --input <plan.json>",
            ]
        return [
            f"HINT: policy v1 の {phase} では先に canonical plan を登録してから advance を呼んでください。",
            (
                "HINT: 正しい呼び出し例: mission-state.py planning reselect "
                "または mission-state.py plan-import --invocation-id <invocation-id> --input <provider-result.json>"
            ),
        ]
    if command_name == "advance" and reason_code == "producing-artifact":
        return _advance_producing_examples()
    if command_name == "review-finalize" and reason_code == "missing-input-ref":
        iteration = context.get("iteration")
        input_ref = context.get("latest_review_input_ref") or context.get("latest_review_input_path")
        min_reviewers = context.get("min_reviewers") or context.get("reviewer_count")
        return [
            "HINT: review-import が返した review_evidence_ref.path を --input-ref で 1 件以上渡してください。",
            f"HINT: 正しい呼び出し例: {_review_finalize_example(iteration, input_ref, min_reviewers)}",
        ]
    if command_name == "review-finalize" and reason_code == "min-reviewers":
        iteration = context.get("iteration")
        input_ref = context.get("latest_review_input_ref") or context.get("latest_review_input_path")
        reviewer_count = context.get("reviewer_count")
        min_reviewers = reviewer_count if isinstance(reviewer_count, int) and not isinstance(reviewer_count, bool) and reviewer_count > 0 else 2
        return [
            f"HINT: 現 state の reviewer_count={min_reviewers} に合わせて --min-reviewers {min_reviewers} を指定してください。",
            f"HINT: 正しい呼び出し例: {_review_finalize_example(iteration, input_ref, min_reviewers)}",
        ]
    if command_name == "review-finalize" and reason_code == "resubmit-reason-missing":
        iteration = context.get("iteration")
        input_ref = context.get("latest_review_input_ref") or context.get("latest_review_input_path")
        return [
            "HINT: 同一 iteration を再 push するときは --resubmit-reason \"<理由>\" を付けてください。",
            f"HINT: 正しい呼び出し例: {_review_finalize_example(iteration, input_ref, context.get('min_reviewers'))} --resubmit-reason \"<理由>\"",
        ]
    if command_name == "review-import" and reason_code == "schema-invalid":
        return [
            f"HINT: 必須トップレベルキー: {_review_import_keys()}",
            f"HINT: 正しい呼び出し例: {_review_import_example(context.get('iteration'))}",
        ]
    if command_name == "lease" and reason_code == "lease-rejected":
        return [
            "HINT: init が返した lease_id を MISSION_LEASE_ID として次の mutating command に渡してください。",
        ]
    if command_name == "set" and reason_code == "reviewer-count":
        return [
            "HINT: `reviewer_count` は complexity または review_tier と同時に更新してください。",
            "HINT: 正しい呼び出し例: mission-state.py set complexity=Critical reviewer_count=4",
        ]
    if command_name == "set" and reason_code == "halt-category":
        return [
            "HINT: `halt_category` の変更は mark-halt / refresh-pid / resume で行ってください。",
            "HINT: 正しい呼び出し例: mission-state.py mark-halt --reason \"<理由>\" --category blocked-external",
        ]
    if command_name == "set" and reason_code == "halt-reason":
        return [
            "HINT: `halt_reason` の変更は reactivate --approved-by-user で行ってください。",
            "HINT: 正しい呼び出し例: mission-state.py reactivate --approved-by-user --reason \"<理由>\" --expected-category <category>",
        ]
    if command_name == "session" and reason_code == "pid-fallback":
        return [
            "HINT: MISSION_SESSION_ID を明示して pid フォールバックを避けてください。",
        ]
    return [
        f"HINT: mission-state.py {command_name} の失敗理由 {reason_code or '<unknown>'} を確認して再実行してください。",
    ]
