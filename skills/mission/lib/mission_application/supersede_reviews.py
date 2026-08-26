"""Application use case for superseding obsolete review generations."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json

from mission_application.lifecycle import (
    SupersedeReviewWriteRequest,
    TERMINALIZABLE_ACTIVE,
    TERMINALIZABLE_UNDECODABLE,
    diagnose_terminalizable_state,
    prepare_supersede_review_write,
    real_terminalizable_state,
    supersede_review_projection,
)


class SupersedeReviewsFailure(ValueError):
    """A stable CLI rejection produced by the supersede use case."""


@dataclass(frozen=True)
class SupersedeReviewsRequest:
    group: object
    cwd: object


@dataclass(frozen=True)
class SupersedeReviewsServices:
    iter_state_files: object
    session_directory: object
    path_lstat: object
    path_read_bytes: object
    path_resolve: object
    is_regular_mode: object
    load_authoritative_state: object
    inspect_repository_bytes: object
    v5_format: object
    repository_selection_error: object
    compatibility_operation_arguments: object
    canonical_compatibility_operation: object
    repository_factory: object
    resolve_session_id: object
    presented_lease_unset: object
    v5_repository_type: object
    legacy_repository_type: object
    fenced_error: object
    command_outcome_exit: object
    reject_fenced_lease: object
    clock: object
    transition_phase: object
    write_terminal_outcome: object
    terminal_paths: set
    printer: object
    stderr: object


@dataclass(frozen=True)
class SupersedeReviewsResult:
    group: str
    current_generation: int
    superseded: tuple
    rendered: str


def _capture_state_file(path, cwd, services):
    metadata = services.path_lstat(path)
    if (
        not services.is_regular_mode(metadata.st_mode)
        or metadata.st_nlink != 1
        or path.parent != services.session_directory(cwd)
    ):
        raise ValueError("review state path is unsafe")
    payload = services.path_read_bytes(path)
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    return payload, identity


def _state_file_unchanged(path, cwd, identity, payload, services):
    current_payload, current_identity = _capture_state_file(path, cwd, services)
    return current_identity == identity and current_payload == payload


def _review_group_members(request, services):
    members = []
    try:
        for state_path in services.iter_state_files(request.cwd):
            payload, identity = _capture_state_file(
                state_path, request.cwd, services
            )
            _snapshot, state = services.load_authoritative_state(
                state_path,
                legacy_compatibility=True,
            )
            if state.get("review_group_id") != request.group:
                continue
            generation = state.get("review_generation")
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
            ):
                raise ValueError("review group has an invalid generation")
            members.append((generation, state_path, state, payload, identity))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SupersedeReviewsFailure(str(error)) from error
    return members


def _classify_review_group(request, members, services):
    if not members:
        raise SupersedeReviewsFailure("review group was not found")
    current_generation = max(item[0] for item in members)
    current = [item for item in members if item[0] == current_generation]
    if len(current) != 1:
        raise SupersedeReviewsFailure(
            "review group has no single current generation"
        )
    targets = [item for item in members if item[0] < current_generation]
    if not all(
        _state_file_unchanged(path, request.cwd, identity, payload, services)
        for _, path, _, payload, identity in members
    ):
        raise SupersedeReviewsFailure(
            "review group changed during supersede preflight"
        )
    return current_generation, current, targets


def _group_target_digest(members):
    target = json.dumps(
        [
            {
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "session_id": state.get("session_id") or state_path.stem,
            }
            for _, state_path, state, payload, _ in sorted(
                members,
                key=lambda item: (item[0], item[1].name),
            )
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(target).hexdigest()


def _requires_caller_operation(members, services):
    try:
        return any(
            services.inspect_repository_bytes(
                payload,
                expected_session_id=state.get("session_id") or state_path.stem,
            ).format
            is services.v5_format
            for _, state_path, state, payload, _ in members
        )
    except services.repository_selection_error as error:
        raise SupersedeReviewsFailure(str(error)) from error


def _repositories(
    request,
    services,
    members,
    current,
    targets,
    current_generation,
    group_target_digest,
    requires_caller_operation,
):
    current_path = current[0][1]
    caller_session_id = services.resolve_session_id()
    repositories = []
    for generation, state_path, state, _payload, _identity in targets + current:
        role = "current" if state_path == current_path else "superseded"
        target_session_id = state.get("session_id") or state_path.stem
        already_superseded = role == "superseded" and (
            state.get("terminal_outcome") == "stale_superseded"
            and state.get("passes") is False
            and state.get("loop_active") is False
            and state.get("halt_category") == "stale"
        )
        try:
            caller_operation_id, operation_arguments = (
                services.compatibility_operation_arguments(
                    {
                        "current_generation": current_generation,
                        "group": request.group,
                        "role": role,
                    },
                    target_digest=group_target_digest,
                    require_caller=requires_caller_operation,
                )
            )
            operation_id, operation_command = (
                services.canonical_compatibility_operation(
                    target_session_id,
                    "supersede-reviews",
                    operation_arguments,
                    caller_operation_id=caller_operation_id,
                )
            )
        except ValueError as error:
            raise SupersedeReviewsFailure(str(error)) from error
        repository = services.repository_factory(
            request.cwd,
            state_path,
            stamp=False,
            strict_read=True,
            session_id=target_session_id,
            operation_id=operation_id,
            operation_command=operation_command,
            operation_command_type="supersede-reviews",
            lease_owner_session_id=caller_session_id,
            presented_lease_id=(
                services.presented_lease_unset if role == "current" else None
            ),
        )
        if already_superseded:
            if not isinstance(repository, services.v5_repository_type):
                continue
            try:
                committed = repository.read(target_session_id)
            except services.fenced_error as error:
                services.reject_fenced_lease(error, state_path=state_path)
            if committed.commit.operation_id != operation_id:
                continue
        repositories.append(
            (generation, state_path, role, target_session_id, repository)
        )
    return repositories


def _admit_all_fences(
    request, services, repositories, current_generation
) -> None:
    try:
        for generation, state_path, role, _session_id, repository in repositories:
            try:
                with repository.transaction():
                    state = repository.load()
                    if (
                        state.get("review_group_id") != request.group
                        or state.get("review_generation") != generation
                        or (role == "current")
                        != (generation == current_generation)
                    ):
                        raise ValueError(
                            "review group changed during supersede preflight"
                        )
            except services.fenced_error as error:
                services.reject_fenced_lease(error, state_path=state_path)
    except (OSError, ValueError) as error:
        raise SupersedeReviewsFailure(str(error)) from error


def _proposed_state(state, role, superseded, now, services):
    proposed = copy.deepcopy(state)
    if role == "superseded":
        proposed["passes"] = False
        proposed["loop_active"] = False
        proposed["halt_reason"] = "superseded by a replacement run"
        proposed["halt_category"] = "stale"
        services.transition_phase(
            proposed,
            "halted",
            now,
            terminal_trusted_boundary=True,
        )
        services.write_terminal_outcome(proposed)
    else:
        proposed["supersedes"] = list(superseded)
    proposed["updated_at"] = now
    return proposed


def _publish_review_group(
    request, services, members, targets, repositories
) -> None:
    now = services.clock()
    superseded = tuple(
        state.get("session_id") or state_path.stem
        for _, state_path, state, _, _ in targets
    )
    original_states = {
        str(services.path_resolve(state_path)): copy.deepcopy(state)
        for _, state_path, state, _, _ in members
    }
    committed_legacy = []
    failed_state_path = None
    try:
        for generation, state_path, role, _session_id, repository in repositories:
            failed_state_path = state_path
            with repository.transaction():
                state = repository.load()
                if getattr(repository, "operation_replayed", False):
                    continue
                if (
                    state.get("review_group_id") != request.group
                    or state.get("review_generation") != generation
                ):
                    raise ValueError("review state changed during supersede")
                real_state = None
                if role == "superseded":
                    diagnosis = diagnose_terminalizable_state(state)
                    if diagnosis == TERMINALIZABLE_UNDECODABLE:
                        services.printer(
                            "WARNING: supersede terminalization fell back to the "
                            "synthetic view because %s could not be decoded"
                            % state_path.name,
                            file=services.stderr,
                        )
                    real_state = (
                        real_terminalizable_state(state)
                        if diagnosis == TERMINALIZABLE_ACTIVE
                        else None
                    )
                proposed = _proposed_state(
                    state, role, superseded, now, services
                )
                prepared = prepare_supersede_review_write(
                    state,
                    proposed,
                    SupersedeReviewWriteRequest(
                        role=role,
                        superseded=superseded,
                        at=now,
                        real_state_available=real_state is not None,
                    ),
                )
                path_key = str(services.path_resolve(state_path))
                if role == "superseded":
                    services.terminal_paths.add(path_key)
                try:
                    if prepared.direct_save:
                        repository.save(
                            proposed,
                            backup=False,
                            administrative=True,
                        )
                    else:
                        proposed = supersede_review_projection(
                            repository.execute(
                                prepared.command,
                                backup=False,
                                administrative=True,
                            )
                        )
                    if isinstance(repository, services.legacy_repository_type):
                        committed_legacy.append(
                            (state_path, role, repository)
                        )
                finally:
                    services.terminal_paths.discard(path_key)
    except (
        OSError,
        ValueError,
        services.command_outcome_exit,
        services.fenced_error,
    ) as error:
        rollback_errors = []
        for state_path, role, repository in reversed(committed_legacy):
            path_key = str(services.path_resolve(state_path))
            if role == "superseded":
                services.terminal_paths.add(path_key)
            try:
                with repository.transaction():
                    repository.save(
                        copy.deepcopy(original_states[path_key]),
                        backup=False,
                        administrative=True,
                    )
            except (OSError, ValueError, services.fenced_error) as rollback_error:
                rollback_errors.append(str(rollback_error))
            finally:
                services.terminal_paths.discard(path_key)
        if rollback_errors:
            raise SupersedeReviewsFailure(
                "supersede transaction rollback failed: "
                + "; ".join(rollback_errors)
            ) from error
        if isinstance(error, services.fenced_error) and failed_state_path is not None:
            services.reject_fenced_lease(error, state_path=failed_state_path)
        raise SupersedeReviewsFailure(
            "supersede transaction was rolled back: %s" % error
        ) from error


def run_supersede_reviews(request, services) -> SupersedeReviewsResult:
    """Validate, fence, and publish one review group without a TOCTOU split."""
    group = request.group
    if not isinstance(group, str) or not group or "\x00" in group:
        raise SupersedeReviewsFailure("review group is invalid")

    members = _review_group_members(request, services)
    current_generation, current, targets = _classify_review_group(
        request, members, services
    )
    target_digest = _group_target_digest(members)
    requires_caller = _requires_caller_operation(members, services)
    repositories = _repositories(
        request,
        services,
        members,
        current,
        targets,
        current_generation,
        target_digest,
        requires_caller,
    )
    _admit_all_fences(request, services, repositories, current_generation)
    _publish_review_group(request, services, members, targets, repositories)
    superseded = tuple(
        state.get("session_id") or state_path.stem
        for _, state_path, state, _, _ in targets
    )
    rendered = json.dumps({
        "ok": True,
        "group": group,
        "current_generation": current_generation,
        "superseded": superseded,
    })
    return SupersedeReviewsResult(
        group, current_generation, superseded, rendered
    )
