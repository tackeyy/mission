"""Application use case for legacy v4 mission initialization."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegacyV4InitializationRequest:
    mission: str
    goal_dispatch: object
    files: object
    host_run_id: object
    root_run_id: object
    parent_run_id: object
    child_run_id: object
    logical_group_id: object
    review_group_id: object
    review_perspective: object
    base_sha: object
    head_sha: object
    session_role: object
    force_mission: bool
    artifact_applicability: object
    max_iter: object
    budget_minutes: object
    threshold: object
    issue_ref: object
    complexity: object
    review_tier: object
    new_mission: bool
    new_mission_assumptions_path: object
    lock_state: bool


@dataclass(frozen=True)
class LegacyV4InitializationServices:
    current_directory: object
    resolve_goal_dispatch: object
    ensure_regular_directory_path: object
    parse_files_arg: object
    clock: object
    correlation_id: object
    opaque_token: object
    mission_id: object
    new_specialist_selection_checkpoint: object
    validated_budget_minutes: object
    normalize_issue_ref: object
    start_phase_default_activity: object
    resolve_session_id: object
    iter_state_files: object
    read_init_peer_state: object
    state_age_since_update_sec: object
    stale_active_seconds: object
    warn_s3_file_overlap: object
    derive_review_tier_decision: object
    pregate_state_reference: object
    pregate_verdict_warning: object
    should_route_init_to_goal: object
    goal_dispatch_route_fields: object
    goal_dispatch_guidance: object
    stamp_metadata: object
    session_dir: object
    session_file: object
    aggregate_file: object
    guarded_init_state_lock: object
    nullcontext: object
    read_legacy_json_file: object
    validate_specialist_public_state: object
    atomic_write_bytes: object
    validated_assumptions_probe_path: object
    close_activity_for_resume: object
    resume_phase_timing: object
    datetime: object
    timezone: object
    move: object
    time: object
    atomic_write_text: object
    backup_state: object
    atomic_write_json: object
    permission_preflight: object
    write_state: object
    exit_init_write_failure: object
    exit_init_evidence_write_failure: object
    exit_internal_invariant: object
    printer: object
    stderr: object
    system_exit: object
    json_dumps: object
    default_max_iter: int
    complexity_reviewer_count: object
    tier_reviewer_count: object
    worktree_archive_error: object
    activity_timing_error: object
    specialist_public_contract_error: object
    json_decode_error: object
    fenced_commit_error: object
    permission_halt_rejected: object


def initialize_legacy_v4(request, services):
    """Initialize one legacy v4 document through injected runtime services."""
    cwd = services.current_directory()
    goal_dispatch = services.resolve_goal_dispatch(
        request.mission,
        request.goal_dispatch,
        cwd,
    )
    try:
        mission_state_root = services.ensure_regular_directory_path(
            cwd, (".mission-state",)
        )
        mission_state_root.mkdir(parents=True, exist_ok=True)
    except (OSError, services.worktree_archive_error):
        services.exit_init_write_failure(cwd)
    planned_files = services.parse_files_arg(request.files)
    now = services.clock()
    try:
        host_run_id = services.correlation_id(request.host_run_id)
        root_run_id = services.correlation_id(request.root_run_id or host_run_id)
        parent_run_id = (
            services.correlation_id(request.parent_run_id)
            if request.parent_run_id
            else None
        )
        child_run_id = (
            services.correlation_id(request.child_run_id)
            if request.child_run_id
            else None
        )
        logical_group_id = (
            services.opaque_token(request.logical_group_id)
            if request.logical_group_id is not None
            else None
        )
        review_group_id = (
            services.opaque_token(request.review_group_id)
            if request.review_group_id is not None
            else None
        )
    except ValueError as error:
        services.printer(f"ERROR: {error}", file=services.stderr)
        services.system_exit(2)

    initial = {
        "mission": request.mission,
        "mission_id": services.mission_id(request.mission),
        "host_run_id": host_run_id,
        "root_run_id": root_run_id,
        "parent_run_id": parent_run_id,
        "child_run_id": child_run_id,
        "logical_group_id": logical_group_id,
        "review_group_id": review_group_id,
        "review_generation": 1 if review_group_id else None,
        "review_perspective": request.review_perspective,
        "base_sha": request.base_sha,
        "head_sha": request.head_sha,
        "supersedes": [],
        "goal_dispatch_requested": goal_dispatch["mode"],
        "goal_dispatch_source": goal_dispatch["source"],
        **(
            {"goal_dispatch_resolution_fallback_reason": goal_dispatch["fallback_reason"]}
            if goal_dispatch.get("fallback_reason")
            else {}
        ),
        "session_role": request.session_role or "implementer",
        **({"force_mission": True} if request.force_mission else {}),
        "subtasks": [],
        "complexity": "Unknown",
        "reviewer_count": 2,
        "task_profile": {},
        "artifact_applicability": request.artifact_applicability,
        "specialists_mode": "auto",
        "specialists_candidates": [],
        "specialists_selected": [],
        "specialists_unavailable": [],
        "specialists_decision": services.new_specialist_selection_checkpoint(),
        "specialist_invocations": [],
        "planning_policy_version": 1,
        "max_iter": (
            services.default_max_iter
            if request.max_iter is None
            else (None if request.max_iter == 0 else request.max_iter)
        ),
        "budget_minutes": services.validated_budget_minutes(request.budget_minutes),
        "threshold": request.threshold,
        "iteration": 0,
        "phase": "planning",
        "score_history": [],
        "stagnation_count": 0,
        "decisions": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": now,
        "updated_at": now,
        "phase_started_at": now,
        "phase_durations_sec": {},
        "activity_current": None,
        "activity_segments": [],
        "activity_rollup": {
            "observed_total_sec": 0.0,
            "closed_segment_count": 0,
            "activity_duration_totals_sec": {},
            "phase_activity_duration_totals_sec": {},
            "wait_reason_totals_sec": {},
        },
        "activity_unobserved_gap_sec": 0.0,
        "activity_unobserved_gap_reasons_sec": {},
        "issue_ref": request.issue_ref,
        "issue_ref_key": services.normalize_issue_ref(request.issue_ref),
        "planned_files": planned_files,
    }
    services.start_phase_default_activity(initial, now)
    issue_ref = request.issue_ref
    issue_ref_key = services.normalize_issue_ref(issue_ref)
    current_session_id = services.resolve_session_id()
    if issue_ref_key:
        for other_state_file in services.iter_state_files(cwd):
            try:
                other = services.read_init_peer_state(other_state_file)
            except Exception:
                continue
            if other.get("session_id") == current_session_id:
                continue
            other_key = other.get("issue_ref_key") or services.normalize_issue_ref(
                other.get("issue_ref")
            )
            if other_key != issue_ref_key:
                continue
            if other.get("passes") is True:
                continue
            if other.get("loop_active"):
                state_label = "active"
            else:
                age = services.state_age_since_update_sec(other)
                stale = age is not None and age >= services.stale_active_seconds()
                state_label = "halted/stale" if stale else "halted"
            hint = " stale の場合は claim を引き継げます。" if "stale" in state_label else ""
            services.printer(
                f"WARNING [S3]: issue_ref='{issue_ref}' を持つ未完了 session が既に存在します"
                f" (session_id={other.get('session_id', '?')}, 状態={state_label})。"
                f"重複作業の可能性を確認してください。{hint}",
                file=services.stderr,
            )
            break
    services.warn_s3_file_overlap(cwd, planned_files, current_session_id)
    if request.complexity:
        initial["complexity"] = request.complexity
        initial["reviewer_count"] = services.complexity_reviewer_count[
            request.complexity
        ]
    else:
        services.printer(
            "WARNING: --complexity 未指定のため 'Unknown' のままです。"
            " Phase 1 判定後に `mission-state.py set complexity=<Simple|Standard|Complex|Critical> reviewer_count=<N>` で必ず更新してください。",
            file=services.stderr,
        )
    user_tier = request.review_tier
    if user_tier:
        initial["review_tier"] = user_tier
        initial["review_tier_source"] = "user"
        initial["review_tier_signals"] = []
        initial["review_tier_signal_details"] = []
    else:
        auto_decision = services.derive_review_tier_decision(
            request.mission,
            initial.get("complexity"),
        )
        initial["review_tier"] = auto_decision["tier"]
        initial["review_tier_source"] = "auto"
        initial["review_tier_signals"] = auto_decision["signals"]
        initial["review_tier_signal_details"] = auto_decision["signal_details"]
    initial["reviewer_count"] = services.tier_reviewer_count[initial["review_tier"]]
    pregate = services.pregate_state_reference(cwd, request.issue_ref)
    if pregate is not None:
        initial["pregate"] = pregate
        pregate_warning = services.pregate_verdict_warning(pregate)
        if pregate_warning:
            services.printer(pregate_warning, file=services.stderr)

    if services.should_route_init_to_goal(
        complexity=initial.get("complexity"),
        force_mission=request.force_mission,
        new_mission=request.new_mission,
        user_tier=user_tier,
        review_tier_signals=initial.get("review_tier_signals"),
        issue_ref=request.issue_ref,
    ):
        dispatch_fields = services.goal_dispatch_route_fields(initial)
        services.printer(
            services.json_dumps(
                {
                    "route": "goal",
                    "complexity": "Simple",
                    "mission_id": initial["mission_id"],
                    "reason": "Simple complexity with no irreversible/security signals (#276)",
                    "guidance": services.goal_dispatch_guidance(
                        dispatch_fields, "mission ループを起動しない。"
                    ),
                    **dispatch_fields,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    initial = services.stamp_metadata(initial, cwd)
    sid = initial["session_id"]
    initial["assumptions_path"] = (
        request.new_mission_assumptions_path
        or f".mission-state/sessions/{sid}-assumptions.md"
    )
    session_directory = services.session_dir(cwd)
    state_target = services.session_file(cwd, sid)
    try:
        services.ensure_regular_directory_path(cwd, (".mission-state", "sessions"))
        session_directory.mkdir(parents=True, exist_ok=True)
    except (OSError, services.worktree_archive_error):
        services.exit_init_write_failure(cwd, state_target)
    aggregate = services.aggregate_file(cwd)
    init_lock = (
        services.guarded_init_state_lock(cwd, state_target)
        if request.lock_state
        else services.nullcontext()
    )
    with init_lock:
        existing_aggregate = {}
        if aggregate.exists():
            try:
                existing_aggregate = services.read_legacy_json_file(aggregate)
            except services.json_decode_error:
                existing_aggregate = {}
        if state_target.exists():
            existing_mission_id = ""
            try:
                existing_data = services.read_legacy_json_file(state_target)
                services.validate_specialist_public_state(existing_data)
                existing_mission_id = existing_data.get("mission_id", "")
                new_mission_id = initial.get("mission_id", "")
                if (
                    existing_mission_id
                    and new_mission_id
                    and existing_mission_id != new_mission_id
                ):
                    try:
                        archive_directory = services.ensure_regular_directory_path(
                            cwd, (".mission-state", "archive")
                        )
                        archive_directory.mkdir(parents=True, exist_ok=True)
                    except (OSError, services.worktree_archive_error) as error:
                        services.printer(
                            f"ERROR: archive destination is unsafe: {error}",
                            file=services.stderr,
                        )
                        services.system_exit(2)
                    old_mission_id_prefix = (
                        existing_mission_id[:8]
                        if len(existing_mission_id) >= 8
                        else existing_mission_id
                    )
                    archive_destination = archive_directory / (
                        f"state-{sid}-{old_mission_id_prefix}.json"
                    )
                    try:
                        services.atomic_write_bytes(
                            archive_destination, state_target.read_bytes()
                        )
                    except OSError:
                        services.exit_init_evidence_write_failure("archive")
                    old_assumptions_path = existing_data.get("assumptions_path")
                    if old_assumptions_path:
                        try:
                            old_assumptions = services.validated_assumptions_probe_path(
                                cwd, str(old_assumptions_path)
                            )
                        except FileNotFoundError:
                            old_assumptions = None
                        except (OSError, ValueError) as error:
                            services.printer(
                                f"ERROR: 旧ミッション assumptions の退避対象が不正です: {error}",
                                file=services.stderr,
                            )
                            services.system_exit(2)
                        if old_assumptions is not None:
                            assumptions_archive = archive_directory / (
                                f"state-{sid}-{old_mission_id_prefix}-assumptions.md"
                            )
                            try:
                                services.atomic_write_bytes(
                                    assumptions_archive, old_assumptions.read_bytes()
                                )
                            except OSError:
                                services.exit_init_evidence_write_failure("archive")
                    initial["assumptions_path"] = (
                        f".mission-state/sessions/{sid}-{new_mission_id[:8]}-"
                        f"{services.time.time_ns()}-assumptions.md"
                    )
                elif existing_mission_id and existing_mission_id == new_mission_id:
                    if "planning_policy_version" not in existing_data:
                        initial.pop("planning_policy_version", None)
                    else:
                        initial["planning_policy_version"] = existing_data[
                            "planning_policy_version"
                        ]
                    existing_assumptions_path = existing_data.get("assumptions_path")
                    if existing_assumptions_path:
                        initial["assumptions_path"] = existing_assumptions_path
                    current = existing_data.get("activity_current")
                    if not (
                        isinstance(current, dict) and current.get("started_at") == now
                    ):
                        services.close_activity_for_resume(existing_data, now)
                    services.resume_phase_timing(existing_data, now)
                    for key in (
                        "activity_current",
                        "activity_segments",
                        "activity_rollup",
                        "activity_unobserved_gap_sec",
                        "activity_unobserved_gap_reasons_sec",
                        "activity_anomaly_counts",
                        "phase_durations_sec",
                        "phase",
                        "phase_started_at",
                        "pregate",
                    ):
                        if key in existing_data:
                            initial[key] = existing_data[key]
                    if pregate is not None:
                        initial["pregate"] = pregate
                    if initial.get("loop_active") is not False and not initial.get(
                        "activity_current"
                    ):
                        services.start_phase_default_activity(initial, now)
            except services.activity_timing_error as error:
                services.printer(
                    f"ERROR: existing mission timing is invalid: {error}",
                    file=services.stderr,
                )
                services.system_exit(2)
            except services.specialist_public_contract_error:
                raise
            except services.json_decode_error as error:
                quarantine_suffix = services.datetime.now(
                    services.timezone.utc
                ).strftime("%Y%m%dT%H%M%SZ")
                quarantine = state_target.with_name(
                    f"{state_target.name}.corrupt-{quarantine_suffix}"
                )
                try:
                    services.move(str(state_target), str(quarantine))
                    services.printer(
                        f"WARNING: 破損した session JSON を退避しました: {quarantine} ({error})",
                        file=services.stderr,
                    )
                except Exception as move_error:
                    services.printer(
                        "WARNING: 破損した session JSON の退避に失敗しました。"
                        f"上書きで復旧します: {move_error}",
                        file=services.stderr,
                    )
            except Exception as error:
                services.printer(
                    f"WARNING: 旧ミッション (id={existing_mission_id[:8]}) のアーカイブに失敗。"
                    f"履歴消失の可能性: {error}",
                    file=services.stderr,
                )
        assumptions_file = cwd / initial["assumptions_path"]
        try:
            if assumptions_file.exists():
                services.validated_assumptions_probe_path(
                    cwd, str(initial["assumptions_path"])
                )
            else:
                services.atomic_write_text(
                    assumptions_file, "# Assumption Registry\n"
                )
        except (OSError, ValueError):
            services.exit_init_evidence_write_failure("assumptions")
        if initial["review_group_id"]:
            prior_generations = []
            for state_path in services.iter_state_files(cwd):
                try:
                    prior = services.read_init_peer_state(state_path)
                except (OSError, ValueError, services.fenced_commit_error):
                    continue
                if prior.get("review_group_id") != initial["review_group_id"]:
                    continue
                generation = prior.get("review_generation")
                if (
                    isinstance(generation, int)
                    and not isinstance(generation, bool)
                    and generation > 0
                ):
                    prior_generations.append(generation)
            initial["review_generation"] = max(prior_generations, default=0) + 1
        services.backup_state(state_target)
        services.write_state(state_target, initial)
        existing_aggregate.setdefault("active_sessions", [])
        if sid not in existing_aggregate["active_sessions"]:
            existing_aggregate["active_sessions"].append(sid)
        existing_aggregate["updated_at"] = services.clock()
        services.atomic_write_json(aggregate, existing_aggregate)
    try:
        permission_preflight = services.permission_preflight(cwd)
    except services.permission_halt_rejected as error:
        services.exit_internal_invariant(error.code, str(error))
    except services.fenced_commit_error as error:
        if error.code not in {"transition-divergence", "transition-unsealed"}:
            raise
        services.exit_internal_invariant(error.code, error.detail)
    if not permission_preflight["ok"]:
        services.printer(
            services.json_dumps(permission_preflight, ensure_ascii=False)
        )
        services.system_exit(2)
    services.printer(
        services.json_dumps(
            {
                "ok": True,
                "mode": "multi-session",
                "session_file": str(state_target),
                "session_id": sid,
                "mission_id": initial["mission_id"],
                "lease_id": initial["lease_id"],
                "fencing_epoch": initial["fencing_epoch"],
                "lease_expires_at": initial["lease_expires_at"],
                "permission_preflight": "passed",
            }
        )
    )


run_initialize_legacy_v4 = initialize_legacy_v4
