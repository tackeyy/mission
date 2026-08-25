"""Application use case for aggregate-reviews."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True, init=False)
class ReviewAggregationRequest:
    iteration: int
    input: tuple
    input_refs: tuple
    out: object
    min_reviewers: object
    reviewer_windows: tuple
    record_outcome: bool
    json: bool
    base_sha: object
    head_sha: object
    event_id: object
    root_event_id: object
    attempt: object
    retry_of: object

    def __init__(
        self, *, iteration, raw_input, raw_input_refs, out, min_reviewers,
        raw_reviewer_windows, record_outcome, json, base_sha, head_sha,
        event_id, root_event_id, raw_attempt, retry_of,
    ):
        object.__setattr__(self, "iteration", iteration)
        object.__setattr__(self, "input", tuple(raw_input or ()))
        object.__setattr__(self, "input_refs", tuple(raw_input_refs or ()))
        object.__setattr__(self, "out", out)
        object.__setattr__(self, "min_reviewers", min_reviewers)
        object.__setattr__(self, "reviewer_windows", tuple(raw_reviewer_windows or ()))
        object.__setattr__(self, "record_outcome", record_outcome)
        object.__setattr__(self, "json", json)
        object.__setattr__(self, "base_sha", base_sha)
        object.__setattr__(self, "head_sha", head_sha)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "root_event_id", root_event_id)
        object.__setattr__(self, "attempt", 1 if raw_attempt is Ellipsis else raw_attempt)
        object.__setattr__(self, "retry_of", retry_of)


@dataclass(frozen=True)
class ReviewAggregationServices:
    current_directory: object
    resolve_state_file: object
    path_exists: object
    load_authoritative_state: object
    command_outcome: object
    revision_scope_from_args: object
    validate_revision_scope: object
    load_review_json: object
    load_imported_review: object
    printer: object
    stderr: object
    system_exit: object
    build_guidance: object
    apply_reviewer_caps: object
    reduce_reviews_to_score: object
    consensus_score: object
    review_failure: object
    review_score_keys: object
    parse_reviewer_windows: object
    observe_parallel_execution: object
    record_command_outcome_only: object
    emit_json_command_failure: object
    command_outcome_emission_target: object
    review_prose_bytes_warn: object
    review_prose_ratio_warn: object
    legacy_lifecycle_repository: object
    current_review_lineage: object
    validate_artifact_state_consistency: object
    lint_state_artifact: object
    artifact_contract_error: object
    invalidate_artifact_lint_observation: object
    clock: object
    canonical_artifact_identity_snapshot: object
    end_activity_segment: object
    transition_phase: object
    record_activity_event: object
    append_command_outcome: object
    expected_context_mode: object
    context_manifest_generated: object
    json_dumps: object
    sha256: object
    state_dir: object
    path_from_string: object
    same_publish_target: object
    command_outcome_exit: object
    published_file: object
    publish_review_archive_transaction: object
    publish_output_transaction: object
    verify_published_file: object
    rollback_published_file: object
    published_rollback_recovery_error: object
    close_published_file: object


@dataclass(frozen=True)
class ReviewAggregationResult:
    rendered: str


def _aggregate_reviews(request, services):
    """Aggregate mission-review/1 reviewer JSON into push-score compatible scoring JSON."""
    cwd = services.current_directory()
    sf = services.resolve_state_file(cwd)
    if not services.path_exists(sf):
        services.printer("ERROR: state.json が見つかりません。先に `init` してください。", file=services.stderr)
        raise services.system_exit(1)
    if request.iteration < 1:
        services.printer("ERROR: --iteration は 1 以上で指定してください", file=services.stderr)
        raise services.system_exit(2)
    outcome = services.command_outcome(request, "aggregate-reviews", "ok")
    try:
        revision_scope = services.revision_scope_from_args(request)
        services.validate_revision_scope(cwd, revision_scope)
    except ValueError as exc:
        services.printer(f"ERROR: {exc}", file=services.stderr)
        raise services.system_exit(2)
    try:
        _source_snapshot, source_state = services.load_authoritative_state(sf)
    except Exception as exc:
        services.printer(f"ERROR: authoritative state is unavailable: {exc}", file=services.stderr)
        raise services.system_exit(2)
    # #326: critic scope 記録の hard gate。#309 の guidance 層は next を呼ばない
    # orchestrator に bypass される実測 (disc-v3) があるため、集計側で fail-closed に
    # 強制する。escape hatch は作らない (#240 の合意偽装防止と同思想)。
    if request.iteration >= 2:
        if source_state.get("critic_has_new_scope") is None:
            services.printer(
                "ERROR: iteration >= 2 の集計には critic_has_new_scope の記録が必要です (#326)。"
                " critic の実行計画テーブルから判定し、"
                "`mission-state.py set critic_has_new_scope='false'` (全ステップが既存 finding id のみ)"
                " または `'true'` (new を含む) を実行してから再集計してください。",
                file=services.stderr,
            )
            raise services.command_outcome_exit(2, "expected-gate")
    input_paths = getattr(request, "input", None) or []
    input_refs = getattr(request, "input_refs", None) or []
    if not input_paths and not input_refs:
        services.printer("ERROR: --input または --input-ref を少なくとも 1 件指定してください", file=services.stderr)
        raise services.system_exit(2)
    loaded_reviews = [services.load_review_json(path, request.iteration) for path in input_paths]
    imported_refs: list[dict] = []
    if input_refs:
        try:
            for reference_path in input_refs:
                review, metric, reference = services.load_imported_review(
                    cwd, source_state, reference_path, request.iteration
                )
                loaded_reviews.append((review, metric))
                imported_refs.append(reference)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            services.printer(f"ERROR: review import reference rejected: {exc}", file=services.stderr)
            raise services.system_exit(2)
    reviews = [review for review, _metric in loaded_reviews]
    reviewer_output_metrics = [
        {"perspective": review["perspective"], **metric}
        for review, metric in loaded_reviews
    ]
    for metric in reviewer_output_metrics:
        if (
            metric["prose_bytes"] > services.review_prose_bytes_warn
            or metric["prose_ratio"] > services.review_prose_ratio_warn
        ):
            services.printer(
                "WARN #353: reviewer output exceeds bounded template guidance "
                f"(perspective={metric['perspective']}, "
                f"prose_bytes={metric['prose_bytes']}, "
                f"prose_ratio={metric['prose_ratio']:.3f})",
                file=services.stderr,
            )

    min_reviewers = getattr(request, "min_reviewers", None)
    if min_reviewers is not None and len(reviews) < min_reviewers:
        state_reviewer_count = source_state.get("reviewer_count")
        shortage_guidance = services.build_guidance(
            "review-finalize",
            "min-reviewers",
            {
                "iteration": request.iteration,
                "reviewer_count": state_reviewer_count,
                "latest_review_input_ref": input_refs[0] if input_refs else None,
            },
        )
        services.printer(
            f"ERROR: reviewer 数不足 (期待 {min_reviewers} 名, 実際 {len(reviews)} 名)。"
            " reviewer を追加してやり直してください。",
            file=services.stderr,
        )
        for line in shortage_guidance:
            services.printer(line, file=services.stderr)
        raise services.command_outcome_exit(2, "expected-gate", guidance=shortage_guidance)

    scoring_reviews = [r for r in reviews if r.get("scores") is not None]
    if not scoring_reviews:
        services.printer("ERROR: 採点対象 reviewer がありません (scores:null の検証専任のみ)", file=services.stderr)
        raise services.system_exit(2)

    adjusted_scores = []
    cap_log = []
    excluded = []
    for review in scoring_reviews:
        values = [float(review["scores"][key]) for key in services.review_score_keys]
        same_score_note = str(review.get("same_score_note") or "")
        if len(set(values)) == 1 and ("全体印象" in same_score_note or "overall impression" in same_score_note.lower()):
            excluded.append({"perspective": review["perspective"], "reason": "same-score overall-impression note"})
            continue
        adjusted, caps = services.apply_reviewer_caps(review)
        adjusted_scores.append({"perspective": review["perspective"], "scores": adjusted})
        cap_log.extend(caps)
    if not adjusted_scores:
        services.printer("ERROR: 全採点 reviewer が除外されました (Reviewer 独立性に疑念)", file=services.stderr)
        raise services.command_outcome_exit(2, "expected-gate")

    axis_values = {
        axis: [entry["scores"][axis] for entry in adjusted_scores]
        for axis in services.review_score_keys
    }
    items = {
        axis: round(sum(values) / len(values), 2)
        for axis, values in axis_values.items()
    }
    agreement_detail = {
        axis: {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "delta": round(max(values) - min(values), 2),
        }
        for axis, values in axis_values.items()
    }
    review_agreement = None
    if len(adjusted_scores) >= 2:
        max_delta = max(detail["delta"] for detail in agreement_detail.values())
        review_agreement = services.consensus_score(max_delta)
    open_high = sum(
        1
        for review in reviews
        for finding in review.get("findings", [])
        if finding.get("severity") == "High"
    )
    # The archive claim is authored by the same pure reducer that later
    # validates the untrusted archive.  Keep the surrounding observability
    # fields, but do not let this writer become a second scoring authority.
    try:
        reduced = services.reduce_reviews_to_score(
            reviews,
            expected_iteration=request.iteration,
        )
    except (services.review_failure, ValueError) as exc:
        services.printer(f"ERROR: review aggregate inputs are invalid: {exc}", file=services.stderr)
        raise services.system_exit(2)
    derived_score = {
        "items": reduced.items,
        "composite": reduced.composite,
        "min_item": reduced.min_item,
        "open_high": reduced.open_high,
        "review_agreement": reduced.review_agreement,
        "agreement_detail": reduced.agreement_detail,
    }
    items = reduced.items
    open_high = reduced.open_high
    review_agreement = reduced.review_agreement
    agreement_detail = reduced.agreement_detail

    # #282/#350: reviewer 並列実行の観測。2 名以上では全 reviewer の
    # self-report を fail-closed で要求し、実行形態そのものは gate しない。
    valid_perspectives = {review["perspective"] for review in reviews}
    reviewer_windows = services.parse_reviewer_windows(
        getattr(request, "reviewer_windows", []) or [], valid_perspectives
    )
    if len(reviews) >= 2:
        reported_perspectives = {window["perspective"] for window in reviewer_windows}
        missing_perspectives = sorted(valid_perspectives - reported_perspectives)
        if missing_perspectives:
            gate_outcome = services.command_outcome(request, "aggregate-reviews", "expected-gate")
            if getattr(request, "record_outcome", True):
                services.record_command_outcome_only(cwd, gate_outcome)
            services.emit_json_command_failure(
                services.command_outcome_emission_target,
                gate_outcome,
            )
            services.printer(
                "ERROR: reviewer window の報告が不足しています。"
                f"不足 perspective: {', '.join(missing_perspectives)}。"
                "報告書式: --reviewer-window <perspective>=<start>..<end>。"
                "#350: 並列実行の検証可能性のため必須",
                file=services.stderr,
            )
            raise services.command_outcome_exit(2, "expected-gate")
    parallel_execution = services.observe_parallel_execution(reviewer_windows)
    reviewer_windows_public = [
        {k: v for k, v in window.items() if not k.startswith("_")}
        for window in reviewer_windows
    ]
    if parallel_execution is False:
        services.printer(
            "WARN: reviewer が直列実行されています (実行時間帯の重なりなし)。"
            "Claude Code では Reviewer を単一メッセージで並列起動してください (#282)。"
            "この warn は観測のみで集計・gate には影響しません。",
            file=services.stderr,
        )

    # #612: archive / scoring JSON の公開より前に lease を検証する (lease-first)。
    # 従来は公開後の repository.save() で初めて admission が走り、拒否時は
    # rollback で回収していたが、#475 の契約は「検証前に公開しない」であり
    # 「公開しても回収する」ではない。plan-import (#498) と同じ修正パターン。
    repository = services.legacy_lifecycle_repository(
        cwd,
        sf,
        stamp=True,
        strict_read=True,
        lease_reason="aggregate-reviews",
        pre_admit_lease=True,
    )
    with repository.transaction():
        data = repository.load()
        # The preflight read above is intentionally advisory only. Re-run all
        # state-dependent reviewer gates against the transaction-bound state so
        # a concurrent critic-scope or reviewer-policy update cannot be
        # bypassed by a stale read.
        if request.iteration >= 2 and data.get("critic_has_new_scope") is None:
            services.printer(
                "ERROR: iteration >= 2 の集計には critic_has_new_scope の記録が必要です (#326)。"
                " critic の実行計画テーブルから判定し、"
                "`mission-state.py set critic_has_new_scope='false'` (全ステップが既存 finding id のみ)"
                " または `'true'` (new を含む) を実行してから再集計してください。",
                file=services.stderr,
            )
            raise services.command_outcome_exit(2, "expected-gate")
        if min_reviewers is not None and len(reviews) < min_reviewers:
            shortage_guidance = services.build_guidance(
                "review-finalize",
                "min-reviewers",
                {
                    "iteration": request.iteration,
                    "reviewer_count": data.get("reviewer_count"),
                    "latest_review_input_ref": input_refs[0] if input_refs else None,
                },
            )
            services.printer(
                f"ERROR: reviewer 数不足 (期待 {min_reviewers} 名, 実際 {len(reviews)} 名)。"
                " reviewer を追加してやり直してください。",
                file=services.stderr,
            )
            for line in shortage_guidance:
                services.printer(line, file=services.stderr)
            raise services.command_outcome_exit(
                2,
                "expected-gate",
                guidance=shortage_guidance,
            )
        if input_refs:
            try:
                current_imported_refs = [
                    services.load_imported_review(cwd, data, path, request.iteration)[2]
                    for path in input_refs
                ]
            except (OSError, ValueError) as exc:
                services.printer(f"ERROR: review import reference rejected: {exc}", file=services.stderr)
                raise services.system_exit(2)
            if current_imported_refs != imported_refs:
                services.printer(
                    "ERROR: review import references changed during aggregation",
                    file=services.stderr,
                )
                raise services.system_exit(2)
        try:
            review_lineage = services.current_review_lineage(cwd, data, revision_scope)
        except ValueError as exc:
            services.printer(f"ERROR: {exc}", file=services.stderr)
            raise services.system_exit(2)
        try:
            services.validate_artifact_state_consistency(data, require_resolved=True)
            artifact_lint, artifact_lint_status = services.lint_state_artifact(cwd, data)
        except services.artifact_contract_error as exc:
            services.invalidate_artifact_lint_observation(data)
            data["artifact_lint_status"] = "invalid"
            data["updated_at"] = services.clock()
            repository.save(data)
            services.printer(f"ERROR: {exc}", file=services.stderr)
            raise services.system_exit(2)
        if artifact_lint_status not in {"clean", "findings"}:
            data.pop("artifact_lint", None)
        else:
            data["artifact_lint"] = artifact_lint
        data["artifact_lint_status"] = artifact_lint_status
        identity_snapshot = services.canonical_artifact_identity_snapshot(data)
        if artifact_lint_status in {"clean", "findings"} and identity_snapshot:
            data["artifact_lint_identity"] = identity_snapshot
        else:
            data.pop("artifact_lint_identity", None)
        for finding in artifact_lint:
            services.printer(
                "WARN #351: artifact lint: "
                f"{finding['kind']} at {finding['heading']}",
                file=services.stderr,
            )
        # #338: 観測結果を state へ永続化し stats で横断集計可能にする (gate 不変)
        data["last_parallel_execution"] = parallel_execution
        if data.get("phase") == "reviewing":
            now = services.clock()
            if isinstance(data.get("activity_current"), dict):
                services.end_activity_segment(data, now)
            services.transition_phase(data, "scoring", now)
            services.record_activity_event(data, "review-aggregate", now)
            data["updated_at"] = now
        prior_metrics = [
            record for record in data.get("reviewer_output_records", [])
            if isinstance(record, dict) and record.get("iteration") != request.iteration
        ]
        data["reviewer_output_records"] = prior_metrics + [
            {"iteration": request.iteration, **metric}
            for metric in reviewer_output_metrics
        ]
        if getattr(request, "record_outcome", True):
            services.append_command_outcome(data, outcome)
        context_mode_expected = services.expected_context_mode(data, request.iteration)
        context_manifest_generated = services.context_manifest_generated(data, request.iteration)
        if context_mode_expected == "bounded" and not context_manifest_generated:
            services.printer(
                "WARN #352: bounded context expected but no manifest generated",
                file=services.stderr,
            )
        mission8 = (data.get("mission_id") or "unknown")[:8]
        evidence = {
            "schema": "mission-review-aggregate/1",
            "iteration": request.iteration,
            "inputs": reviews,
            "input_refs": imported_refs,
            "scoring_perspectives": [entry["perspective"] for entry in adjusted_scores],
            "excluded": excluded,
            "cap_log": cap_log,
            "agreement_detail": agreement_detail,
            "open_high": open_high,
            "reviewer_windows": reviewer_windows_public,
            "parallel_execution": parallel_execution,
            "artifact_lint": artifact_lint,
            "artifact_lint_status": artifact_lint_status,
            "context_mode_expected": context_mode_expected,
            "context_manifest_generated": context_manifest_generated,
            "reviewer_output_metrics": reviewer_output_metrics,
            # This is the authoritative, deterministic derivation from the
            # archived review inputs. push-score and mark-passes compare every
            # decision value to it; a digest alone is not semantic binding.
            "score_claim": {
                "iteration": request.iteration, **derived_score,
            },
        }
        evidence_content = (services.json_dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        evidence_digest = "sha256:" + services.sha256(evidence_content).hexdigest()
        evidence_path = services.state_dir(cwd) / "archive" / f"iter-{request.iteration}-{mission8}-reviews-{evidence_digest[7:23]}.json"
        evidence_ref_path = str(evidence_path.relative_to(cwd))

        out_path = (
            services.path_from_string(request.out)
            if request.out
            else services.state_dir(cwd) / "tmp" / f"mission-scorer-iter-{request.iteration}-{mission8}.json"
        )
        if services.same_publish_target(out_path, evidence_path):
            raise services.command_outcome_exit(2, "invalid-input")
        payload = {
            "items": items,
            "notes": f"aggregate-reviews: {len(adjusted_scores)} scoring reviewer(s), {len(reviews) - len(scoring_reviews)} findings-only reviewer(s)",
            "open_high": open_high,
            "findings_evidence_path": evidence_ref_path,
            "review_agreement": review_agreement,
            "agreement_detail": agreement_detail,
            "score_provenance": {
                "score_source": "scoring-json",
                "review_evidence_ref": {
                    "kind": "review-aggregate", "path": evidence_ref_path,
                    "digest": evidence_digest,
                    "generation": evidence_digest[7:23],
                    "revision_scope": revision_scope,
                    **(review_lineage or {}),
                },
                "revision_scope": revision_scope,
            },
        }
        payload_content = (services.json_dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        archive_publish: services.published_file | None = None
        out_publish: services.published_file | None = None
        try:
            archive_publish = services.publish_review_archive_transaction(
                cwd, evidence_path.name, evidence_content,
            )
            if archive_publish.path != evidence_path:
                raise ValueError("review aggregate archive path mismatch")
            out_publish = services.publish_output_transaction(
                out_path,
                payload_content,
                forbidden_targets=((archive_publish.directory_identity, archive_publish.path.name),),
            )
            services.verify_published_file(archive_publish)
            services.verify_published_file(out_publish)
            repository.save(data)
        except BaseException as exc:
            recovery_error: services.published_rollback_recovery_error | None = None
            if out_publish is not None:
                try:
                    services.rollback_published_file(out_publish)
                except services.published_rollback_recovery_error as rollback_error:
                    recovery_error = rollback_error
                except ValueError as rollback_error:
                    services.printer(f"ERROR: aggregate output rollback rejected: {rollback_error}", file=services.stderr)
                out_publish = None
            if archive_publish is not None:
                try:
                    services.rollback_published_file(archive_publish)
                except services.published_rollback_recovery_error as rollback_error:
                    if recovery_error is None:
                        recovery_error = rollback_error
                except ValueError as rollback_error:
                    services.printer(f"ERROR: aggregate archive rollback rejected: {rollback_error}", file=services.stderr)
                archive_publish = None
            if recovery_error is not None:
                raise recovery_error from exc
            if isinstance(exc, ValueError):
                services.printer(f"ERROR: aggregate output rejected: {exc}", file=services.stderr)
                raise services.command_outcome_exit(2, "invalid-input") from exc
            raise
        finally:
            if out_publish is not None:
                services.close_published_file(out_publish)
            if archive_publish is not None:
                services.close_published_file(archive_publish)

    result = {
        "ok": True,
        "outcome_kind": "ok",
        "outcome": outcome,
        "out": str(out_path),
        "findings_evidence_path": str(evidence_path),
        "open_high": open_high,
        "items": items,
        "review_agreement": review_agreement,
        "parallel_execution": parallel_execution,
        "artifact_lint": artifact_lint,
        "artifact_lint_status": artifact_lint_status,
    }
    return result


def run_aggregate_reviews(request, services):
    result = _aggregate_reviews(request, services)
    return ReviewAggregationResult(services.json_dumps(result, ensure_ascii=False, indent=2) if request.json else str(result["out"]))
