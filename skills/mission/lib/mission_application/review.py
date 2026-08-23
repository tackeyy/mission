"""Review, score, and completion application boundary."""

from __future__ import annotations

import copy
import math
import json
import re
from dataclasses import dataclass, replace
from typing import Callable

from mission_kernel.codec_v4 import decode_mission_state
from mission_kernel.commands import MarkPass
from mission_kernel.model import (
    BoundScore,
    GitRevisionScope,
    ManualScoreRef,
    NotApplicableRevisionScope,
    ReviewInputRef,
)
from mission_kernel.transitions import Decision, decide, transition_control_claim_bounds
from scoring_provenance import reduce_review_aggregate as _canonical_review_reduction

from .ports import LegacyMissionRepository


REVIEW_COMMAND_OWNERS = {
    "aggregate-reviews": "A2.review",
    "closeout": "A2.review",
    "manual-score-capture": "A2.review",
    "mark-passes": "A2.review",
    "push-score": "A2.review",
    "review-finalize": "A2.review",
    "review-import": "A2.review",
    "supersede-reviews": "A2.review",
}


class ReviewFailure(ValueError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SCORE_AXES = (
    "mission_achievement", "accuracy", "completeness", "usability",
)


def typed_review_input_ref(value: dict) -> ReviewInputRef:
    """Close an adapter-produced review reference before it reaches state."""
    required = {"kind", "path", "digest", "size", "iteration", "perspective"}
    if not isinstance(value, dict) or set(value) != required or value.get("kind") != "review-input":
        raise ReviewFailure("review input reference has invalid fields", reason="review-ref-invalid")
    if (
        not isinstance(value["path"], str)
        or not value["path"]
        or _DIGEST.fullmatch(str(value["digest"])) is None
        or isinstance(value["size"], bool)
        or not isinstance(value["size"], int)
        or value["size"] <= 0
        or isinstance(value["iteration"], bool)
        or not isinstance(value["iteration"], int)
        or value["iteration"] < 1
        or not isinstance(value["perspective"], str)
        or not value["perspective"]
    ):
        raise ReviewFailure("review input reference is invalid", reason="review-ref-invalid")
    return ReviewInputRef(
        relative_path=value["path"],
        digest=value["digest"],
        size=value["size"],
        iteration=value["iteration"],
        perspective=value["perspective"],
    )


def legacy_review_input_ref(reference: ReviewInputRef) -> dict:
    return {
        "kind": reference.kind.value,
        "path": reference.relative_path,
        "digest": reference.digest,
        "size": reference.size,
        "iteration": reference.iteration,
        "perspective": reference.perspective,
    }


def _typed_revision_scope(value: dict):
    if not isinstance(value, dict):
        raise ReviewFailure("revision scope is invalid", reason="revision-scope-invalid")
    if value.get("kind") == "git" and set(value) == {"kind", "base_sha", "head_sha"}:
        base_sha, head_sha = value["base_sha"], value["head_sha"]
        if (
            not isinstance(base_sha, str)
            or _GIT_SHA.fullmatch(base_sha) is None
            or not isinstance(head_sha, str)
            or _GIT_SHA.fullmatch(head_sha) is None
        ):
            raise ReviewFailure("revision scope is invalid", reason="revision-scope-invalid")
        return GitRevisionScope(base_sha, head_sha)
    if value.get("kind") == "not-applicable" and set(value) == {"kind", "reason_code"}:
        reason_code = value["reason_code"]
        if not isinstance(reason_code, str) or not reason_code:
            raise ReviewFailure("revision scope is invalid", reason="revision-scope-invalid")
        return NotApplicableRevisionScope(reason_code)
    raise ReviewFailure("revision scope is invalid", reason="revision-scope-invalid")


def typed_manual_score_ref(value: dict) -> ManualScoreRef:
    required = {"kind", "path", "digest", "generation", "revision_scope"}
    if not isinstance(value, dict) or set(value) != required or value.get("kind") != "manual-score":
        raise ReviewFailure("manual score reference has invalid fields", reason="manual-ref-invalid")
    if (
        not isinstance(value["path"], str)
        or not value["path"]
        or _DIGEST.fullmatch(str(value["digest"])) is None
        or not isinstance(value["generation"], str)
        or not value["generation"]
    ):
        raise ReviewFailure("manual score reference is invalid", reason="manual-ref-invalid")
    return ManualScoreRef(
        relative_path=value["path"],
        digest=value["digest"],
        generation=value["generation"],
        revision_scope=_typed_revision_scope(value["revision_scope"]),
    )


def legacy_manual_score_ref(reference: ManualScoreRef) -> dict:
    scope = reference.revision_scope
    if isinstance(scope, GitRevisionScope):
        revision_scope = {"kind": "git", "base_sha": scope.base_sha, "head_sha": scope.head_sha}
    else:
        revision_scope = {"kind": "not-applicable", "reason_code": scope.reason_code}
    return {
        "kind": reference.kind,
        "path": reference.relative_path,
        "digest": reference.digest,
        "generation": reference.generation,
        "revision_scope": revision_scope,
    }


@dataclass(frozen=True)
class ReducedReviewScore:
    items: dict[str, float]
    composite: float
    min_item: float
    open_high: int
    review_agreement: float | None
    agreement_detail: dict[str, dict[str, float]]


def reduce_reviews_to_score(
    reviews: list[dict],
    *,
    expected_iteration: int,
) -> ReducedReviewScore:
    """Own the single review-to-score reduction use case.

    The canonical reducer is sealed inside the use case so an adapter cannot
    inject a pre-decided score. Every decision value is then revalidated.
    """
    try:
        derived = _canonical_review_reduction(
            reviews, expected_iteration=expected_iteration
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewFailure(
            "review reduction input is invalid", reason="review-reduction-invalid"
        ) from exc
    required = {
        "items",
        "composite",
        "min_item",
        "open_high",
        "review_agreement",
        "agreement_detail",
    }
    if not isinstance(derived, dict) or set(derived) != required:
        raise ReviewFailure("review reduction has invalid fields", reason="review-reduction-invalid")
    items = derived["items"]
    if not isinstance(items, dict) or set(items) != set(_SCORE_AXES):
        raise ReviewFailure("review reduction items are invalid", reason="review-reduction-invalid")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 5.0
        for value in items.values()
    ):
        raise ReviewFailure("review reduction items are invalid", reason="review-reduction-invalid")
    normalized_items = {key: float(items[key]) for key in _SCORE_AXES}
    expected_composite = round(sum(normalized_items.values()) / len(normalized_items), 2)
    expected_min_item = round(min(normalized_items.values()), 2)
    for field, expected_value in (
        ("composite", expected_composite),
        ("min_item", expected_min_item),
    ):
        value = derived[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) != expected_value
        ):
            raise ReviewFailure("review reduction score is invalid", reason="review-reduction-invalid")
    open_high = derived["open_high"]
    if not isinstance(reviews, list):
        raise ReviewFailure("review reduction input is invalid", reason="review-reduction-invalid")
    expected_open_high = 0
    for review in reviews:
        if not isinstance(review, dict):
            raise ReviewFailure("review reduction input is invalid", reason="review-reduction-invalid")
        findings = review.get("findings", [])
        if not isinstance(findings, list):
            raise ReviewFailure("review reduction input is invalid", reason="review-reduction-invalid")
        expected_open_high += sum(
            1
            for finding in findings
            if isinstance(finding, dict) and finding.get("severity") == "High"
        )
    if (
        isinstance(open_high, bool)
        or not isinstance(open_high, int)
        or open_high != expected_open_high
    ):
        raise ReviewFailure("review reduction open_high is invalid", reason="review-reduction-invalid")
    agreement = derived["review_agreement"]
    if agreement is not None and (
        isinstance(agreement, bool)
        or not isinstance(agreement, (int, float))
        or not math.isfinite(float(agreement))
    ):
        raise ReviewFailure("review reduction agreement is invalid", reason="review-reduction-invalid")
    detail = derived["agreement_detail"]
    if not isinstance(detail, dict) or set(detail) != set(_SCORE_AXES):
        raise ReviewFailure("review reduction agreement detail is invalid", reason="review-reduction-invalid")
    normalized_detail = {}
    for axis in _SCORE_AXES:
        axis_detail = detail[axis]
        if (
            not isinstance(axis_detail, dict)
            or set(axis_detail) != {"min", "max", "delta"}
        ):
            raise ReviewFailure("review reduction agreement detail is invalid", reason="review-reduction-invalid")
        if any(
            isinstance(axis_detail[field], bool)
            or not isinstance(axis_detail[field], (int, float))
            or not math.isfinite(float(axis_detail[field]))
            for field in ("min", "max", "delta")
        ):
            raise ReviewFailure("review reduction agreement detail is invalid", reason="review-reduction-invalid")
        minimum = float(axis_detail["min"])
        maximum = float(axis_detail["max"])
        delta = float(axis_detail["delta"])
        if (
            not 0.0 <= minimum <= maximum <= 5.0
            or delta != round(maximum - minimum, 2)
            or not minimum <= normalized_items[axis] <= maximum
        ):
            raise ReviewFailure("review reduction agreement detail is invalid", reason="review-reduction-invalid")
        normalized_detail[axis] = {"min": minimum, "max": maximum, "delta": delta}
    max_delta = max(value["delta"] for value in normalized_detail.values())
    expected_agreement = (
        5 if max_delta <= 0.5
        else 4 if max_delta <= 1.0
        else 3 if max_delta <= 1.5
        else 2 if max_delta <= 2.0
        else 1
    )
    if agreement is None:
        if max_delta != 0.0:
            raise ReviewFailure("review reduction agreement is invalid", reason="review-reduction-invalid")
    elif float(agreement) != float(expected_agreement):
        raise ReviewFailure("review reduction agreement is invalid", reason="review-reduction-invalid")
    reduced = ReducedReviewScore(
        items=normalized_items,
        composite=expected_composite,
        min_item=expected_min_item,
        open_high=open_high,
        review_agreement=None if agreement is None else float(agreement),
        agreement_detail=normalized_detail,
    )
    return reduced


@dataclass(frozen=True)
class MarkPassRequest:
    force: bool
    reason: str | None
    approved_by_user: bool
    specialist_waiver: str
    at: str


@dataclass(frozen=True)
class MarkPassServices:
    verify_force_approval: Callable[[dict], dict]
    validate_force_terminal: Callable[[dict, dict], None]
    validate_score_evidence: Callable[[dict, dict], None]
    validate_artifact_gate: Callable[[dict], None]
    validate_specialist_gate: Callable[[dict, str], None]
    transition_phase: Callable[[dict, str, str], None]
    write_terminal_outcome: Callable[[dict], None]
    optional_unclosed_skills: Callable[[dict], list[str]]
    selection_id: Callable[[dict], object]
    # #568: early-stop の継続条件の評価結果を返す観測子。gate 判定には使わない
    # (記録のみ)。未配線の adapter では None を許し、記録を省略する。
    early_stop_evaluation: Callable[[dict, dict | None, str], dict | None] | None = None


@dataclass(frozen=True)
class MarkPassResult:
    forced: bool
    force_approval: dict | None
    unclosed_skills: tuple[str, ...]
    decision: Decision


def _kernel_state_for_pass(data: dict, latest_index: int | None):
    try:
        raw = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        state = decode_mission_state(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewFailure(f"typed state decode failed: {exc}", reason="invalid-state") from exc
    if latest_index is None:
        return state
    if latest_index >= len(state.scores):
        raise ReviewFailure("score projection is incomplete", reason="authoritative-score-required")
    score = state.scores[latest_index]
    if not isinstance(score, BoundScore):
        raise ReviewFailure("score provenance is not authoritative", reason="authoritative-score-required")
    authoritative = replace(score, authoritative=True)
    return replace(
        state,
        scores=(*state.scores[:latest_index], authoritative, *state.scores[latest_index + 1 :]),
    )


def _latest_declared_score(data: dict) -> tuple[int | None, dict | None]:
    history = data.get("score_history")
    if not isinstance(history, list):
        return None, None
    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        if isinstance(entry, dict) and "composite" in entry:
            return index, entry
    return None, None


_PASS_REJECTION_MESSAGES = {
    "score-required": "採点未実施。`push-score` を先に呼んでください。",
    "authoritative-score-required": "score provenance is not authoritative",
    "invalid-open-high": "open_high must be a non-negative integer",
    "open-high-findings": "未解決 High があるため合格にできません。High 指摘を全て解消してから再採点してください。",
    "composite-below-threshold": "composite が threshold 未満のため合格にできません。",
    "minimum-item-below-threshold": "min_item が 3.5 未満のため合格にできません。",
    "review-agreement-too-low": "低合意: 争点軸の追加レビューを実施して再集計してください。",
    "artifact-gate-unsatisfied": "artifact gate is not satisfied",
    "specialist-gate-unsatisfied": "specialist gate is not satisfied",
    "force-approval-required": "force approval evidence is required",
}


def _maximum_agreement(payload: dict) -> tuple[str | None, float | None]:
    detail = payload.get("agreement_detail")
    if not isinstance(detail, dict):
        return None, None
    selected_axis = None
    selected_delta = None
    for axis, raw in detail.items():
        if not isinstance(raw, dict):
            continue
        delta = raw.get("delta")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)):
            continue
        value = float(delta)
        if selected_delta is None or value > selected_delta:
            selected_axis = str(axis)
            selected_delta = value
    return selected_axis, selected_delta


def _pass_rejection_message(reason: str, data: dict, latest: dict | None) -> str:
    if latest is None:
        return _PASS_REJECTION_MESSAGES.get(reason, reason)
    if reason == "open-high-findings":
        count = latest.get("open_high")
        return (
            f"未解決 High が {count} 件あるため合格にできません。"
            "High 指摘を全て解消してから再採点してください。"
        )
    if reason == "composite-below-threshold":
        return (
            f"composite {latest.get('composite')} < threshold {data.get('threshold', 4.0)} "
            "のため合格にできません。Critic を起動し次イテレーションへ進んでください。"
        )
    if reason == "minimum-item-below-threshold":
        return (
            f"min_item {latest.get('min_item')} < 3.5 のため合格にできません "
            "(採点した items のいずれかが 3.5 未満)。Critic を起動し次イテレーションへ進んでください。"
        )
    if reason == "review-agreement-too-low":
        axis, delta = _maximum_agreement(latest)
        rendered = "unknown" if delta is None else f"{delta:.2f}"
        return (
            f"低合意: 争点軸 {axis} の追加レビュー 1 名を実施して再集計してください "
            f"(max-min={rendered})"
        )
    return _PASS_REJECTION_MESSAGES.get(reason, reason)


def mark_pass(
    repository: LegacyMissionRepository,
    request: MarkPassRequest,
    services: MarkPassServices,
) -> MarkPassResult:
    """Validate evidence, ask the kernel for completion, then persist v4 bytes."""
    if request.force and not request.reason:
        raise ReviewFailure(
            '--force を指定する場合は --reason "<理由>" が必須です。',
            reason="force-reason-required",
        )
    if request.force and request.approved_by_user is not True:
        raise ReviewFailure(
            "--force を指定する場合は --approved-by-user も必須です (#185)。",
            reason="force-user-approval-required",
        )

    with repository.transaction():
        data = repository.load()
        verification = services.verify_force_approval(data) if request.force else None
        try:
            services.validate_artifact_gate(data)
        except ValueError as exc:
            raise ReviewFailure(str(exc), reason="artifact-gate-unsatisfied") from exc
        latest_index, latest = _latest_declared_score(data)
        if not request.force:
            if latest is None:
                raise ReviewFailure(
                    _PASS_REJECTION_MESSAGES["score-required"], reason="score-required"
                )
            try:
                services.validate_score_evidence(data, latest)
            except ValueError as exc:
                raise ReviewFailure(f"provenance: {exc}", reason="score-evidence-invalid") from exc
            try:
                services.validate_specialist_gate(data, request.specialist_waiver)
            except ValueError as exc:
                raise ReviewFailure(str(exc), reason="specialist-gate-unsatisfied") from exc
        typed_state = _kernel_state_for_pass(data, None if request.force else latest_index)
        decision = decide(
            typed_state,
            MarkPass(
                force=request.force,
                force_approval_verified=verification is not None,
                artifact_gate_satisfied=True,
                specialist_gate_satisfied=not request.force,
            ),
        )
        if not decision.accepted or decision.transition is None:
            reason = decision.rejection.code if decision.rejection else "pass-rejected"
            raise ReviewFailure(_pass_rejection_message(reason, data, latest), reason=reason)
        claimed = set(transition_control_claim_bounds(decision.transition))

        def mutate(proposed: dict) -> None:
            if "passes" not in claimed:
                proposed["passes"] = True
            if "loop_active" not in claimed:
                proposed["loop_active"] = False
            proposed["passes_forced"] = request.force
            services.transition_phase(proposed, "done", request.at)
            proposed["updated_at"] = request.at
            if request.force:
                proposed["force_reason"] = request.reason
                proposed["force_approved_by_user"] = request.approved_by_user
                proposed["force_approval"] = verification
            elif request.specialist_waiver:
                proposed["specialist_waiver"] = {
                    "reason": request.specialist_waiver,
                    "selection_id": services.selection_id(data),
                    "recorded_at": request.at,
                }
            # #568: 「なぜ iter N で継続しなかったか」を事後監査できるようにする。
            # decide() の後に置き、pass gate の入力にしない (記録のみ)。
            #
            # 観測子の失敗が gate を巻き添えにしてはならない。例外は握り潰さず、
            # 「観測に失敗した」ことを state に記録して pass 判定は続行する
            # (記録の欠落と観測の失敗を区別できるようにする)。
            if not request.force and services.early_stop_evaluation is not None:
                try:
                    evaluation = services.early_stop_evaluation(data, latest, request.at)
                except Exception as exc:  # noqa: BLE001 - observation must not abort the gate
                    evaluation = {
                        "decision": "stop",
                        "status": "observation-failed",
                        "error": type(exc).__name__,
                        "recorded_at": request.at,
                    }
                if evaluation is not None:
                    proposed["early_stop_evaluation"] = evaluation

        def finalize(proposed: dict) -> None:
            if request.force:
                services.validate_force_terminal(proposed, verification)
                proposed["force_approval"]["consumed"] = True

        proposed = (
            repository.execute(data, mutate, decision.transition, finalize)
            if request.force
            else repository.execute(data, mutate, decision.transition)
        )
        repository.save(proposed, aggregate_action="remove")
        unclosed = [] if request.force else services.optional_unclosed_skills(proposed)
    return MarkPassResult(
        forced=request.force,
        force_approval=verification,
        unclosed_skills=tuple(unclosed),
        decision=decision,
    )
