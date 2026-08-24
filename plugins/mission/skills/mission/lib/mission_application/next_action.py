"""Application policy for deriving the next mission action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from mission_common import HALT_CATEGORIES
from planning_lifecycle import derive_planning_lifecycle
from specialist_accounting import (
    explicitly_selected_specialist_skills,
    terminal_invoked_specialist_skills,
)


class NextStateView(Protocol):
    terminal_outcome: str | None
    halt_reason: str
    halt_category: str
    passes: bool
    awaiting_user: bool
    loop_active: bool
    phase: str
    iteration: int


@dataclass(frozen=True)
class NextActionRequest:
    document: Mapping[str, object]
    authoritative: NextStateView


@dataclass(frozen=True)
class NextActionServices:
    pregate_warning: Callable[[object], str | None]
    goal_dispatch_fields: Callable[[Mapping[str, object]], Mapping[str, object]]
    goal_dispatch_guidance: Callable[[Mapping[str, object]], str]
    expected_context_mode: Callable[[Mapping[str, object], int], str]
    valid_composite: Callable[[object], bool]


def _halt_category_for_confirmation(value: object) -> str:
    """Normalize a persisted category for approval matching without mutation."""
    if isinstance(value, str) and value in HALT_CATEGORIES:
        return value
    return "unknown"


def _is_legacy_stale_halt(category: object, reason: object) -> bool:
    """Recognize pre-category stale/orphan state across recovery paths."""
    category_is_legacy = category is None or category == "" or category == "unknown"
    return (
        category_is_legacy
        and isinstance(reason, str)
        and reason.startswith(("orphan:", "stale:"))
    )


def _happy_path_sequence(
    phase: str,
    reviewer_count: int,
    *,
    plan_mode: str = "subagent",
    adopt_core: bool = False,
) -> list[str]:
    """Return the happy-path command sequence from the current phase."""
    plan_step = (
        "plan を artifact に記載 (inline #339)"
        if plan_mode == "inline"
        else "Skill: mission-planner"
    )
    steps = [
        plan_step,
        "mission-state.py advance --phase executing --activity active:implementation",
        "Skill: mission-executor",
        "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response",
        f"Skill: mission-reviewer x{reviewer_count} (1 message, parallel)",
        "mission-state.py review-import --iteration <i> --stdin (reviewer ごとに実行し review_evidence_ref.path を保持)",
        f"mission-state.py review-finalize --iteration <i> --input-ref <review_evidence_ref.path> (全 reviewer 分を反復) --min-reviewers {reviewer_count}",
        "mission-state.py closeout",
    ]
    if adopt_core and phase == "planning":
        steps.insert(1, "mission-state.py planning adopt-core --input <plan.json>")
    start = {"planning": 0, "executing": 2, "reviewing": 4}[phase]
    return steps[start:]


def _native_review_handoff_hint(
    iteration: int | str,
    reviewer_count: int | str,
    *,
    resubmit: bool = False,
) -> str:
    """Return staged native commands without temp files or shell composition."""
    resubmit_hint = (
        ' --resubmit-reason "retry with review evidence"' if resubmit else ""
    )
    return (
        f"Step 1 (reviewer ごと): mission-state.py review-import --iteration {iteration} "
        "--stdin; 返却 JSON の review_evidence_ref.path を保持する。 "
        f"Step 2: mission-state.py review-finalize --iteration {iteration} "
        "--input-ref <review_evidence_ref.path> (全 reviewer 分だけ --input-ref を反復) "
        f"--min-reviewers {reviewer_count}{resubmit_hint}。 "
        "Step 3: mission-state.py mark-passes。"
    )


def _unclosed_optional_specialist_skills(data: Mapping[str, object]) -> list[str]:
    selected = explicitly_selected_specialist_skills(data)
    terminal = terminal_invoked_specialist_skills(data)
    return sorted(selected - terminal)


def derive_next_action(
    request: NextActionRequest,
    services: NextActionServices,
) -> dict[str, object]:
    """Derive the next action without adapter or persistence dependencies."""
    data = request.document
    snapshot = request.authoritative
    terminal_outcome = snapshot.terminal_outcome
    if terminal_outcome == "completed_evidence":
        return {
            "next_action": "report-terminal",
            "summary": "証拠提出で正常終了した mission。最終報告では evidence 提出の完了を伝え、passes=true は主張しない",
            "command_hint": "mission-state.py specialists summary",
        }
    halt_reason = snapshot.halt_reason
    if halt_reason:
        halt_category = snapshot.halt_category
        legacy_stale = _is_legacy_stale_halt(halt_category, halt_reason)
        if halt_category == "stale" or legacy_stale:
            recovery_summary = "stale/orphan halt は resume で安全に再開する"
            recovery_hint = "mission-state.py resume"
        else:
            expected_category = _halt_category_for_confirmation(halt_category)
            recovery_summary = "手動 halt は対象操作と state 再活性化の明示承認後に reactivate する"
            recovery_hint = (
                "mission-state.py reactivate --approved-by-user "
                f"--expected-category {expected_category} "
                '--reason "<ユーザーが承認した再開理由>"'
            )
        return {
            "next_action": "report-blocker",
            "summary": f"halted: {halt_reason}。blocker と次アクションをユーザーに報告する。{recovery_summary}",
            "command_hint": recovery_hint,
        }
    if snapshot.passes:
        return {
            "next_action": "report-complete",
            "summary": "mission は合格済み。最終報告 (成果物パス・検証結果・specialist summary) を出して終了する",
            "command_hint": "mission-state.py specialists summary",
        }
    if snapshot.awaiting_user:
        return {
            "next_action": "await-user",
            "summary": "ユーザー回答待ち (awaiting_user=true)。回答を得るまで不可逆操作に進まない",
            "command_hint": "",
        }
    if not snapshot.loop_active:
        return {
            "next_action": "resume",
            "summary": "loop_active=false だが未合格・halt 理由なし。refresh-pid で再活性化してループを再開する",
            "command_hint": "mission-state.py refresh-pid",
        }
    phase = snapshot.phase or "planning"
    iteration = snapshot.iteration or 1
    reviewer_count = data.get("reviewer_count", 2) or 2
    effective_reviewer_count = reviewer_count
    if iteration >= 2 and data.get("critic_has_new_scope") is False:
        effective_reviewer_count = min(reviewer_count, 2)
    pregate_warning = services.pregate_warning(data.get("pregate"))

    def _planning_summary(summary: str) -> str:
        if not pregate_warning:
            return summary
        return f"{summary} {pregate_warning.removeprefix('WARNING: ')}"

    stagnation = data.get("stagnation_count", 0) or 0
    if stagnation >= 3 and phase != "reviewing":
        return {
            "next_action": "consider-halt",
            "summary": f"stagnation_count={stagnation} (3 連続でスコア停滞)。アプローチを変えても改善しない場合は mark-halt で停止し状況を報告する",
            "command_hint": 'mission-state.py mark-halt --reason "<停滞理由>"',
        }
    if (
        phase == "planning"
        and iteration <= 1
        and data.get("complexity") == "Simple"
        and not data.get("review_tier_signals")
        and data.get("review_tier_source") != "user"
        and not data.get("issue_ref")
        and not data.get("force_mission")
        and (data.get("session_role") or "implementer") == "implementer"
        and not (data.get("score_history") or [])
    ):
        dispatch_fields = services.goal_dispatch_fields(data)
        dispatch_guidance = services.goal_dispatch_guidance(dispatch_fields)
        return {
            "next_action": "route-to-goal",
            "summary": (
                f"Simple + リスクシグナルなし: {dispatch_guidance}"
                "state を routed-goal で閉じ (pass-rate 対象外)、最終報告に routing を明記する。"
                "mission 機構が必要なら --force-mission で再 init (#325)"
            ),
            "command_hint": (
                "mission-state.py mark-halt --reason 'routed-to-goal (#325)' "
                f"--category routed-goal → {dispatch_guidance}"
            ),
            "details": {"complexity": "Simple", "route": "goal", **dispatch_fields},
        }
    if phase == "planning":
        lifecycle = derive_planning_lifecycle(data)
        if lifecycle["mode"] == "policy-v1":
            action = lifecycle.get("next_action")
            if action == "reconcile-provider-invocation":
                running = next(
                    record
                    for record in data.get("specialist_invocations") or []
                    if isinstance(record, dict)
                    and record.get("phase") == "planning"
                    and record.get("iteration") == iteration
                    and record.get("status") == "running"
                )
                return {
                    "next_action": action,
                    "summary": "running planning provider must be reconciled before any new planning action",
                    "command_hint": f"mission-state.py specialists reconcile-invocation --invocation-id {running['invocation_id']} --status <completed|failed|abandoned-unknown> --evidence <ref> --expected-fencing-epoch <epoch>",
                }
            if action and action != "run-planner":
                hints = {
                    "prepare-planning-provider": "mission-state.py specialists prepare-invocation ...",
                    "await-planning-approval": "mission-state.py specialists verify-approval --preflight-id <id> --evidence-ref <ref> --approval-verifier <id>",
                    "invoke-planning-provider": "mission-state.py specialists invoke-prepared --provider <provider> --preflight-id <id> --iteration <i> --phase planning",
                    "import-planning-result": "mission-state.py specialists plan-import --input <result> --invocation-id <id>",
                    "promote-canonical-plan": "mission-state.py planning promote-provider-plan --invocation-id <id>",
                    "run-planner-with-evidence": "Skill: mission-planner (provider evidence is advisory only)",
                    "run-executor": "mission-state.py advance --phase executing --activity active:implementation",
                    "halt-required-planning-provider": "mission-state.py mark-halt --category required-planning-provider --reason <reason>",
                    "run-planner": "Skill: mission-planner",
                }
                return {
                    "next_action": action,
                    "summary": _planning_summary("policy v1 returns exactly one gated planning action"),
                    "command_hint": hints[action],
                    "details": {
                        "planning_policy_version": 1,
                        **({"degraded": True} if lifecycle.get("degraded") else {}),
                    },
                }
        core_adoption_required = (
            data.get("planning_policy_version") == 1
            and data.get("planning_strategy") in {None, "core"}
            and data.get("planning_provider_required") is not True
        )
        adoption_hint = (
            " → mission-state.py planning adopt-core --input <plan.json>"
            if core_adoption_required
            else ""
        )
        if (
            data.get("complexity") == "Standard"
            and iteration <= 1
            and (data.get("review_tier") or "standard") != "full"
        ):
            summary = _planning_summary(
                (
                    f"iteration {iteration} (Standard): mission-planner を起動せず、この turn 内で "
                    "bounded plan (steps + 依存関係 + 完了条件) を artifact に書く (#339)。"
                    "計画の成果物要件は subagent 経路と同一"
                )
            )
            return {
                "next_action": "plan-inline",
                "summary": summary,
                "command_hint": (
                    f"plan を artifact に記載{adoption_hint}"
                    " → mission-state.py advance --phase executing --activity active:implementation"
                ),
                "details": {"plan_mode": "inline"},
                "command_sequence": _happy_path_sequence(
                    "planning",
                    effective_reviewer_count,
                    plan_mode="inline",
                    adopt_core=core_adoption_required,
                ),
            }
        return {
            "next_action": "run-planner",
            "summary": _planning_summary(
                f"iteration {iteration}: mission-planner を起動して計画を立てる "
                "(完了後 advance --phase executing)"
            ),
            "command_hint": (
                f"Skill: mission-planner{adoption_hint}"
                " → mission-state.py advance --phase executing --activity active:implementation"
            ),
            "command_sequence": _happy_path_sequence(
                "planning",
                effective_reviewer_count,
                plan_mode="subagent",
                adopt_core=core_adoption_required,
            ),
        }
    if phase == "executing":
        return {
            "next_action": "run-executor",
            "summary": (
                f"iteration {iteration}: mission-executor で計画を実行する "
                "(完了後 advance --phase reviewing。10分超は progress update)"
            ),
            "command_hint": "Skill: mission-executor → mission-state.py advance --phase reviewing --activity reviewer-wait:review-response",
            "command_sequence": _happy_path_sequence(
                "executing", effective_reviewer_count
            ),
        }
    if phase == "reviewing":
        if iteration >= 2 and data.get("critic_has_new_scope") is None:
            return {
                "next_action": "record-critic-scope",
                "summary": (
                    f"iteration {iteration}: reviewer 起動前に critic の実行計画テーブルから "
                    "scope 判定を state へ記録する。全ステップの対応 finding が既存 finding id "
                    "のみなら false、new を含むなら true (#309)"
                ),
                "command_hint": "mission-state.py set critic_has_new_scope='false'  # または 'true'",
                "details": {"iteration": iteration},
            }
        context_mode = services.expected_context_mode(data, iteration)
        return {
            "next_action": "run-reviewers",
            "summary": f"iteration {iteration}: mission-reviewer を {effective_reviewer_count} 名、単一メッセージで並列起動する (直列起動は規律違反。直列は Standard で約 2-3 分の無駄を実測 #338)",
            "command_hint": f"Skill: mission-reviewer x{effective_reviewer_count} (1 message)",
            "details": {
                "reviewer_count": effective_reviewer_count,
                "context_mode": context_mode,
                "parallel_spawn_required": True,
            },
            "command_sequence": _happy_path_sequence(
                "reviewing", effective_reviewer_count
            ),
        }
    history = data.get("score_history") or []
    scored_current = [
        item
        for item in history
        if isinstance(item, dict)
        and item.get("iteration") == iteration
        and services.valid_composite(item.get("composite"))
    ]
    if scored_current:
        latest = scored_current[-1]
        missing_findings_evidence = (
            latest.get("score_source") == "scoring-json"
            and not latest.get("findings_evidence_path")
        )
        if missing_findings_evidence:
            unclosed_during_retry = _unclosed_optional_specialist_skills(data)
            return {
                "next_action": "aggregate-reviews",
                "summary": (
                    f"iteration {iteration}: 直前の push-score に findings evidence "
                    "(findings_evidence_path) がありません。このまま mark-passes を呼んでも "
                    "exit 2 になります。--force は使わず aggregate-reviews からやり直してください。"
                ),
                "command_hint": (
                    _native_review_handoff_hint(
                        iteration, effective_reviewer_count, resubmit=True
                    )
                    + " mission-scorer fallback を使った場合も、その mission-review/1 出力を Step 1 に渡す。"
                ),
                "details": {
                    "missing_findings_evidence": True,
                    **(
                        {"unclosed_specialists": unclosed_during_retry}
                        if unclosed_during_retry
                        else {}
                    ),
                },
            }
        unclosed = _unclosed_optional_specialist_skills(data)
        return {
            "next_action": "mark-passes",
            "summary": f"iteration {iteration} の採点は記録済み。mark-passes で threshold gate 判定する (reject なら mission-critic → 次 iteration)",
            "command_hint": "mission-state.py mark-passes",
            "details": {"unclosed_specialists": unclosed} if unclosed else {},
        }
    return {
        "next_action": "aggregate-reviews",
        "summary": (
            f"iteration {iteration}: reviewer の mission-review/1 JSON を review-import --stdin で"
            " state-owned evidence にし、review-finalize --input-ref で集計・記録する。"
            "--force は使わない。"
        ),
        "command_hint": _native_review_handoff_hint(
            iteration, effective_reviewer_count
        ),
    }
