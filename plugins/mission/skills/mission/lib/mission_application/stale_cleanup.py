"""Application use case for cleanup-stale."""
from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class CleanupStaleRequest:
    root: object
    execute: bool


@dataclass(frozen=True)
class CleanupStaleServices:
    default_search_roots: object
    path_from_string: object
    path_exists: object
    iter_state_files: object
    load_authoritative_state: object
    lease_fields_present: object
    expired_lease_without_heartbeat: object
    project_root_of: object
    terminalize_state_file: object
    fenced_commit_error: object
    pid_is_agent: object
    state_age_since_update_sec: object
    stale_active_seconds: object
    path_stem: object
    path_to_string: object


@dataclass(frozen=True)
class CleanupStaleResult:
    rendered: str


def run_cleanup_stale(request, services):
    """Classify active states and optionally terminalize eligible stale states."""
    if request.root:
        search_roots = [services.path_from_string(request.root)]
    else:
        search_roots = services.default_search_roots()
    results = {
        "halted": [],
        "would_halt": [],
        "skipped": [],
        "errors": [],
        "warnings": [],
        "dry_run": not request.execute,
    }
    pid_sessions: dict[int, list[str]] = {}
    for root in search_roots:
        if not services.path_exists(root):
            continue
        for state_file in services.iter_state_files(root):
            try:
                _snapshot, data = services.load_authoritative_state(
                    state_file,
                    legacy_compatibility=True,
                    allow_missing_schema_session_mismatch=True,
                )
                if not data.get("loop_active"):
                    continue
                if data.get("passes") or data.get("halt_reason"):
                    continue
                if services.lease_fields_present(data):
                    lease_stale, lease_reason = (
                        services.expired_lease_without_heartbeat(data)
                    )
                    if not lease_stale:
                        results["skipped"].append({
                            "path": services.path_to_string(state_file),
                            "reason": lease_reason,
                            "owner_session_id": data.get("owner_session_id"),
                            "lease_expires_at": data.get("lease_expires_at"),
                        })
                        continue
                    project_root = services.project_root_of(state_file)
                    if request.execute:
                        try:
                            halted = services.terminalize_state_file(
                                state_file,
                                project_root,
                                reason=(
                                    "stale: session lease expired without activity heartbeat "
                                    "(cleanup-stale)"
                                ),
                                category="stale",
                                set_terminal_phase=True,
                                require_expired_lease=True,
                            )
                        except services.fenced_commit_error as error:
                            if error.code != "lease-rejected":
                                raise
                            results["skipped"].append({
                                "path": services.path_to_string(state_file),
                                "reason": "lease-rejected",
                                "owner_session_id": data.get("owner_session_id"),
                                "lease_expires_at": data.get("lease_expires_at"),
                            })
                            continue
                        if halted:
                            results["halted"].append({
                                "path": services.path_to_string(state_file),
                                "reason": lease_reason,
                                "owner_session_id": data.get("owner_session_id"),
                            })
                    else:
                        results["would_halt"].append({
                            "path": services.path_to_string(state_file),
                            "reason": lease_reason,
                            "owner_session_id": data.get("owner_session_id"),
                            "mission": (data.get("mission") or "")[:80],
                        })
                    continue
                pid = data.get("pid")
                if not pid:
                    results["skipped"].append({
                        "path": services.path_to_string(state_file),
                        "reason": "no pid",
                    })
                    continue
                try:
                    pid_sessions.setdefault(int(pid), []).append(
                        services.path_stem(state_file)
                    )
                except (TypeError, ValueError):
                    pass
                try:
                    if services.pid_is_agent(int(pid)):
                        stored_root = data.get("project_root", "")
                        if stored_root and not services.path_exists(
                            services.path_from_string(stored_root)
                        ):
                            halt_reason = (
                                f"orphan: project_root not found ({stored_root})"
                                " / update-project-root で救済可能"
                            )
                            project_root = services.project_root_of(state_file)
                            if request.execute:
                                halted = services.terminalize_state_file(
                                    state_file,
                                    project_root,
                                    reason=halt_reason,
                                    category="stale",
                                    set_terminal_phase=False,
                                    expected_pid=pid,
                                    require_missing_root=True,
                                )
                                if halted:
                                    results["halted"].append({
                                        "path": services.path_to_string(state_file),
                                        "pid": pid,
                                    })
                            else:
                                results["would_halt"].append({
                                    "path": services.path_to_string(state_file),
                                    "pid": pid,
                                    "mission": (data.get("mission") or "")[:80],
                                })
                        else:
                            age_sec = services.state_age_since_update_sec(data)
                            stale_threshold = services.stale_active_seconds()
                            role = data.get("session_role") or "implementer"
                            if role != "implementer" and not data.get("score_history"):
                                results["skipped"].append({
                                    "path": services.path_to_string(state_file),
                                    "reason": "checker-role-no-score-by-design",
                                    "pid": pid,
                                    "session_role": role,
                                    "age_sec": age_sec,
                                })
                            elif not data.get("score_history") and (
                                age_sec is None or age_sec >= stale_threshold
                            ):
                                halt_reason = (
                                    "stale: active no-score checkpoint exceeded "
                                    f"{stale_threshold}s with live agent pid {pid} "
                                    "(cleanup-stale)"
                                )
                                project_root = services.project_root_of(state_file)
                                if request.execute:
                                    halted = services.terminalize_state_file(
                                        state_file,
                                        project_root,
                                        reason=halt_reason,
                                        category="stale",
                                        set_terminal_phase=True,
                                        expected_pid=pid,
                                        require_stale_no_score=True,
                                    )
                                    if halted:
                                        results["halted"].append({
                                            "path": services.path_to_string(state_file),
                                            "pid": pid,
                                            "reason": "stale-active-no-score",
                                            "age_sec": age_sec,
                                        })
                                else:
                                    results["would_halt"].append({
                                        "path": services.path_to_string(state_file),
                                        "pid": pid,
                                        "reason": "stale-active-no-score",
                                        "age_sec": age_sec,
                                        "mission": (data.get("mission") or "")[:80],
                                    })
                            else:
                                results["skipped"].append({
                                    "path": services.path_to_string(state_file),
                                    "reason": f"pid {pid} alive (agent)",
                                    "age_sec": age_sec,
                                })
                    else:
                        pid_source = data.get("pid_source", "agent")
                        if pid_source == "fallback":
                            age_sec = services.state_age_since_update_sec(data)
                            stale_threshold = services.stale_active_seconds()
                            if age_sec is not None and age_sec < stale_threshold:
                                results["skipped"].append({
                                    "path": services.path_to_string(state_file),
                                    "reason": "fallback-pid-unobserved",
                                    "pid": pid,
                                    "age_sec": age_sec,
                                })
                            else:
                                project_root = services.project_root_of(state_file)
                                halt_reason = (
                                    f"stale: fallback pid {pid} dead, age {age_sec}s >= "
                                    f"{stale_threshold}s threshold (cleanup-stale)"
                                )
                                if request.execute:
                                    halted = services.terminalize_state_file(
                                        state_file,
                                        project_root,
                                        reason=halt_reason,
                                        category="stale",
                                        set_terminal_phase=True,
                                        expected_pid=pid,
                                    )
                                    if halted:
                                        results["halted"].append({
                                            "path": services.path_to_string(state_file),
                                            "pid": pid,
                                            "reason": "fallback-stale",
                                        })
                                else:
                                    results["would_halt"].append({
                                        "path": services.path_to_string(state_file),
                                        "pid": pid,
                                        "reason": "fallback-stale",
                                        "mission": (data.get("mission") or "")[:80],
                                    })
                        else:
                            project_root = services.project_root_of(state_file)
                            if request.execute:
                                halted = services.terminalize_state_file(
                                    state_file,
                                    project_root,
                                    reason=(
                                        f"orphan: pid {pid} dead or reused "
                                        "(cleanup-stale)"
                                    ),
                                    category="stale",
                                    set_terminal_phase=False,
                                    expected_pid=pid,
                                    require_dead_pid=True,
                                )
                                if halted:
                                    results["halted"].append({
                                        "path": services.path_to_string(state_file),
                                        "pid": pid,
                                        "reason": "orphan-dead-or-reused",
                                    })
                            else:
                                results["would_halt"].append({
                                    "path": services.path_to_string(state_file),
                                    "pid": pid,
                                    "mission": (data.get("mission") or "")[:80],
                                })
                except Exception as error:
                    results["errors"].append({
                        "path": services.path_to_string(state_file),
                        "error": str(error),
                    })
            except Exception as error:
                results["errors"].append({
                    "path": services.path_to_string(state_file),
                    "error": str(error),
                })
    for pid, session_ids in sorted(pid_sessions.items()):
        if len(session_ids) > 1:
            results["warnings"].append({
                "kind": "duplicate-pid",
                "pid": pid,
                "sessions": session_ids,
                "note": (
                    "複数 session が同一 PID を共有 (親プロセス管理下の並列 mission)。"
                    " stale 判定は last_activity_at ベースで行われる (#310/#314)"
                ),
            })
    return CleanupStaleResult(
        rendered=json.dumps(results, indent=2, ensure_ascii=False)
    )
