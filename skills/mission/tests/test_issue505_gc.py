"""Issue #505: reference-safe generation garbage collection."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mission_persistence.gc import GRACE_SECONDS, PURGE_GRACE_SECONDS

from .mission_state_fixture_corpus import generate_cli_state_bytes
from .test_issue503_fenced_commit import (
    _cli_mutation_from_bytes,
    _commit_cli_init,
    _request,
    _with_admitted_takeover_reason,
)
from .test_issue504_crash_recovery import _kill_during_commit


def _generation_path(repository: Path, digest: str) -> Path:
    return repository / "generations" / (digest.removeprefix("sha256:") + ".json")


def _quarantine_path(repository: Path, digest: str) -> Path:
    return (
        repository
        / "transactions"
        / "quarantine"
        / (digest.removeprefix("sha256:") + ".json")
    )


def _age(path: Path, *, now: datetime, seconds: int) -> None:
    timestamp = (now - timedelta(seconds=seconds)).timestamp()
    os.utime(path, (timestamp, timestamp), follow_symlinks=False)


def _commit_next(local, clock, tmp_path: Path, base_bytes: bytes, *, phase: str, suffix: str):
    clock.current += timedelta(seconds=1)
    now = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    lease_id = json.loads(base_bytes)["lease_id"]
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / ("actual-cli-" + suffix),
        base_bytes,
        lease_id=lease_id,
        now=now,
        phase=phase,
    )
    request = _request(
        operation_id="operation-" + suffix,
        lease_id=lease_id,
        argv=("set", "phase=" + phase),
    )
    admitted = local.begin(request)
    target_bytes = _with_admitted_takeover_reason(target_bytes, admitted)
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    result = local.commit(prepared, prepared.precondition)
    return target_bytes, result


def _three_commits(tmp_path: Path, *, fault_injector=None):
    local, repository, clock, _state_path, first_bytes, first = _commit_cli_init(
        tmp_path,
        fault_injector=fault_injector,
    )
    first_head = (repository / "sessions" / "test.json").read_bytes()
    second_bytes, second = _commit_next(
        local,
        clock,
        tmp_path,
        first_bytes,
        phase="reviewing",
        suffix="reviewing",
    )
    second_head = (repository / "sessions" / "test.json").read_bytes()
    third_bytes, third = _commit_next(
        local,
        clock,
        tmp_path,
        second_bytes,
        phase="scoring",
        suffix="scoring",
    )
    return {
        "local": local,
        "repository": repository,
        "clock": clock,
        "state_bytes": (first_bytes, second_bytes, third_bytes),
        "results": (first, second, third),
        "heads": (first_head, second_head),
    }


def _publish_unreferenced_generation(repository: Path, tmp_path: Path) -> str:
    from mission_persistence.local_uow import (
        VerifiedBlobSet,
        publish_generation,
        stage_generation,
    )

    _state_path, state_bytes = generate_cli_state_bytes(tmp_path / "unreferenced-cli")
    staged = stage_generation(
        repository,
        state_bytes=state_bytes,
        effects=(),
        blobs=VerifiedBlobSet(()),
    )
    published = publish_generation(repository, staged)
    return published.generation_digest


def _write_archive_pointer(
    repository: Path,
    tmp_path: Path,
    generation_digest: str,
) -> Path:
    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "archive-cli")
    state = json.loads(state_bytes)
    state["state_generation"] = {
        "digest": generation_digest,
        "path": "generations/" + generation_digest.removeprefix("sha256:") + ".json",
        "size": _generation_path(repository, generation_digest).stat().st_size,
    }
    archived_state = json.dumps(state, indent=2, ensure_ascii=False).encode("utf-8")
    assumptions = state_path.with_name("test-assumptions.md").read_bytes()

    bundle = repository / "archive" / "worktree-fixture"
    staging = bundle / "generations" / "pending"
    (staging / "sessions").mkdir(parents=True)
    (staging / "sessions" / "test.json").write_bytes(archived_state)
    (staging / "sessions" / "test-assumptions.md").write_bytes(assumptions)
    evidence = [
        {
            "session_id": state["session_id"],
            "mission_id": state["mission_id"],
            "iteration": state["iteration"],
            "evidence_kind": "state",
            "source_reference": ".mission-state/sessions/test.json",
            "archive_path": "sessions/test.json",
            "sha256": hashlib.sha256(archived_state).hexdigest(),
            "size": len(archived_state),
        },
        {
            "session_id": state["session_id"],
            "mission_id": state["mission_id"],
            "iteration": state["iteration"],
            "evidence_kind": "assumptions",
            "source_reference": ".mission-state/sessions/test-assumptions.md",
            "archive_path": "sessions/test-assumptions.md",
            "sha256": hashlib.sha256(assumptions).hexdigest(),
            "size": len(assumptions),
        },
    ]
    core = {
        "schema": "mission-worktree-archive/1",
        "session_id": state["session_id"],
        "mission_id": state["mission_id"],
        "iteration": state["iteration"],
        "evidence": evidence,
    }
    archive_generation = hashlib.sha256(
        json.dumps(
            core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        **core,
        "created_at": "2026-08-16T00:00:00Z",
        "content_digest": archive_generation,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    generation_root = staging.with_name(archive_generation)
    staging.replace(generation_root)
    (bundle / "current.json").write_text(
        json.dumps(
            {"schema": "mission-worktree-current/1", "generation": archive_generation}
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle


def _destructive_policy(*, grace_seconds=3600, purge_grace_seconds=86400):
    from mission_persistence.gc import RetentionPolicy

    return RetentionPolicy(
        grace_seconds=grace_seconds,
        purge_grace_seconds=purge_grace_seconds,
        dry_run=False,
        destructive=True,
    )


def test_r1_current_generation_is_never_a_candidate_even_in_dry_run(tmp_path):
    from mission_persistence.gc import RetentionPolicy

    local, repository, clock, _path, _state, result = _commit_cli_init(tmp_path)
    _age(_generation_path(repository, result.state_generation_digest), now=clock.current, seconds=7200)

    report = local.collect(RetentionPolicy())

    assert result.state_generation_digest not in report.candidates
    assert _generation_path(repository, result.state_generation_digest).exists()


def test_r2_and_b3_prior_safety_retains_one_prior_but_not_two_prior(tmp_path):
    from mission_persistence.gc import RetentionPolicy

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    first, second, third = fixture["results"]
    for result in (first, second, third):
        _age(_generation_path(repository, result.state_generation_digest), now=clock.current, seconds=7200)

    report = local.collect(RetentionPolicy())

    # Prior safety keeps the current head and its immediate predecessor; only the
    # older generation may become a GC candidate.
    assert first.state_generation_digest in report.candidates
    assert second.state_generation_digest not in report.candidates
    assert third.state_generation_digest not in report.candidates

    # Destructive mode should physically remove the older, unreferenced prior
    # generation while leaving the current head and the immediate predecessor.
    destructive = local.collect(_destructive_policy())
    assert destructive.quarantined == (first.state_generation_digest,)
    assert not _generation_path(repository, first.state_generation_digest).exists()
    assert _generation_path(repository, second.state_generation_digest).exists()
    assert _generation_path(repository, third.state_generation_digest).exists()


def test_r3_archive_pointer_generation_is_not_a_candidate(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository
    from mission_persistence.gc import RetentionPolicy

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    _write_archive_pointer(repository, tmp_path, digest)
    now = datetime.now(timezone.utc)
    _age(_generation_path(repository, digest), now=now, seconds=7200)
    local.clock = lambda: now

    report = local.collect(RetentionPolicy())

    assert digest not in report.candidates
    assert _generation_path(repository, digest).exists()


def test_r4_open_recovery_record_generation_is_not_a_candidate(tmp_path):
    from mission_persistence.gc import RetentionPolicy

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    third_bytes = fixture["state_bytes"][2]
    clock.current += timedelta(seconds=1)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "prepared-cli",
        third_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="done",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-prepared-root",
            lease_id="fixture-lease",
            argv=("set", "phase=done"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    generation_ref = prepared.staged.generation_digest
    generation_path = _generation_path(repository, generation_ref)

    def interrupt(point):
        if point == "after-generation-publish":
            raise RuntimeError("leave an open recovery root")

    local.fault_injector = interrupt
    with pytest.raises(RuntimeError, match="open recovery root"):
        local.commit(prepared, prepared.precondition)
    local.fault_injector = None
    _age(generation_path, now=clock.current, seconds=7200)

    report = local.collect(RetentionPolicy())

    assert generation_ref not in report.candidates
    assert generation_path.exists()


def test_r5_all_session_heads_are_protected(tmp_path):
    from mission_persistence.gc import RetentionPolicy

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    _first, _second, third = fixture["results"]
    _path, other_bytes = generate_cli_state_bytes(tmp_path / "other-session-cli")
    other_document = json.loads(other_bytes)
    other_document["session_id"] = "other"
    other_document["owner_session_id"] = "other"
    clock.current = datetime.strptime(
        other_document["last_activity_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    other_bytes = json.dumps(
        other_document, indent=2, ensure_ascii=False
    ).encode("utf-8")
    admitted = local.begin(
        _request(
            operation_id="operation-other-session",
            lease_id=other_document["lease_id"],
            argv=("init", "Issue 500 CLI corpus"),
            session_id="other",
            lease_owner_session_id="other",
            command_type="init",
            event_types=("mission-initialized",),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=other_bytes, effects=())
    other = local.commit(prepared, prepared.precondition)
    for result in (other, third):
        _age(_generation_path(repository, result.state_generation_digest), now=clock.current, seconds=7200)

    report = local.collect(RetentionPolicy())

    assert other.state_generation_digest not in report.candidates
    assert third.state_generation_digest not in report.candidates


def test_r6_operations_are_not_traversed_and_remain_byte_identical(tmp_path, monkeypatch):
    import mission_persistence.gc as gc_module
    from mission_persistence.gc import RetentionPolicy

    local, repository, clock, _path, _state, _result = _commit_cli_init(tmp_path)
    before = {
        path.name: path.read_bytes() for path in (repository / "operations").iterdir()
    }
    original_scan = gc_module._scan_directory

    def rejecting_operations(path):
        if path == repository / "operations":
            raise AssertionError("operations must not be traversed")
        return original_scan(path)

    monkeypatch.setattr(gc_module, "_scan_directory", rejecting_operations)
    local.clock = lambda: clock.current + timedelta(days=2)

    local.collect(RetentionPolicy())

    assert before == {
        path.name: path.read_bytes() for path in (repository / "operations").iterdir()
    }


def test_s1b_candidate_mutation_after_revalidation_aborts_without_quarantine(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    first = fixture["results"][0]
    candidate = _generation_path(repository, first.state_generation_digest)
    _age(candidate, now=clock.current, seconds=3601)
    fired = False

    def interrupt(point):
        nonlocal fired
        if point == "before-gc-revalidate" and not fired:
            fired = True
            candidate.write_bytes(candidate.read_bytes() + b"tamper")

    local.fault_injector = interrupt

    with pytest.raises(FencedCommitError) as excinfo:
        local.collect(_destructive_policy())

    assert excinfo.value.code == "gc-digest-mismatch"
    # The mutation is detected before quarantine, so the source file remains in
    # place and no quarantine entry is created.
    assert candidate.exists()
    assert not _quarantine_path(repository, first.state_generation_digest).exists()


def test_c1_d1_d3_aged_unreferenced_generation_requires_destructive_mode(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository
    from mission_persistence.gc import RetentionPolicy

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=3601)

    dry_run = local.collect(RetentionPolicy())
    # Dry-run must classify the generation without touching the filesystem.
    assert _generation_path(repository, digest).exists()
    destructive = local.collect(_destructive_policy())

    assert dry_run.dry_run is True
    assert dry_run.candidates == (digest,)
    assert _quarantine_path(repository, digest).exists()
    assert destructive.quarantined == (digest,)
    assert not _generation_path(repository, digest).exists()


def test_c2_c3_old_operation_replays_after_generation_quarantine_and_collision_rejects(tmp_path):
    from mission_persistence.fenced_commit import CommitResult, FencedCommitError

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    first = fixture["results"][0]
    _age(_generation_path(repository, first.state_generation_digest), now=clock.current, seconds=3601)
    local.collect(_destructive_policy())

    replay = local.begin(
        _request(
            operation_id="operation-init",
            lease_id="fixture-lease",
            argv=("init", "Issue 500 CLI corpus"),
            command_type="init",
            event_types=("mission-initialized",),
        )
    )
    with pytest.raises(FencedCommitError) as collision:
        local.begin(
            _request(
                operation_id="operation-init",
                lease_id="fixture-lease",
                argv=("set", "phase=done"),
            )
        )

    assert isinstance(replay, CommitResult)
    assert replay == first
    assert collision.value.code == "operation-intent-collision"


@pytest.mark.parametrize(
    ("age_seconds", "expected_candidate"),
    ((GRACE_SECONDS - 1, False), (GRACE_SECONDS + 1, True)),
    ids=("grace-minus-one", "grace-plus-one"),
)
def test_c4_b1_generation_grace_boundary_pair(tmp_path, age_seconds, expected_candidate):
    from mission_persistence.fenced_commit import LocalFencedRepository
    from mission_persistence.gc import RetentionPolicy

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=age_seconds)

    report = local.collect(RetentionPolicy())

    assert (digest in report.candidates) is expected_candidate


def test_c5_changed_quarantine_bytes_are_reported_and_not_purged(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=3601)
    local.collect(_destructive_policy())
    quarantined = _quarantine_path(repository, digest)
    quarantined.write_bytes(quarantined.read_bytes() + b"changed")
    local.clock = lambda: datetime.fromtimestamp(
        quarantined.stat().st_ctime, timezone.utc
    ) + timedelta(seconds=86401)

    report = local.collect(_destructive_policy())

    # A changed quarantine record is recorded, but the bytes mismatch makes it
    # ineligible for purge on this pass.
    assert report.changed == (digest,)
    assert quarantined.exists()


def test_s1_head_movement_between_scan_and_quarantine_cancels_deletion(tmp_path):
    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    first = fixture["results"][0]
    first_head = fixture["heads"][0]
    _age(_generation_path(repository, first.state_generation_digest), now=clock.current, seconds=3601)
    moved = False

    def move_head(point):
        nonlocal moved
        if point == "before-gc-revalidate" and not moved:
            moved = True
            (repository / "sessions" / "test.json").write_bytes(first_head)

    local.fault_injector = move_head

    report = local.collect(_destructive_policy())

    assert first.state_generation_digest not in report.quarantined
    assert _generation_path(repository, first.state_generation_digest).exists()


def test_s2_symlink_in_generation_scan_aborts_without_deletion(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)
    os.symlink(candidate, repository / "generations" / ("f" * 64 + ".json"))

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()
    assert not _quarantine_path(repository, digest).exists()


def test_s2_hard_link_in_generation_scan_aborts_without_deletion(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)
    hard_link = tmp_path / "generation-hard-link.json"
    os.link(candidate, hard_link)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()
    assert hard_link.exists()


def test_s2_generation_parent_swap_cannot_move_an_external_file(tmp_path, monkeypatch):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)

    generations = repository / "generations"
    detached = repository / "generations-detached"
    outside = tmp_path / "outside-generations"
    outside.mkdir()
    outside_candidate = outside / candidate.name
    outside_candidate.write_bytes(candidate.read_bytes())
    original_replace = os.replace
    swapped = False

    def swap_parent_then_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(source).name == candidate.name:
            swapped = True
            generations.rename(detached)
            os.symlink(outside, generations)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", swap_parent_then_replace)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert swapped is True
    assert outside_candidate.exists()


def test_s2_quarantine_parent_swap_cannot_unlink_an_external_file(tmp_path, monkeypatch):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    generation = _generation_path(repository, digest)
    _age(generation, now=now, seconds=3601)
    local.collect(_destructive_policy())

    quarantine = repository / "transactions" / "quarantine"
    detached = repository / "transactions" / "quarantine-detached"
    quarantined = _quarantine_path(repository, digest)
    outside = tmp_path / "outside-quarantine"
    outside.mkdir()
    outside_candidate = outside / quarantined.name
    outside_candidate.write_bytes(quarantined.read_bytes())
    local.clock = lambda: datetime.fromtimestamp(
        quarantined.stat().st_ctime, timezone.utc
    ) + timedelta(seconds=86401)
    original_unlink = os.unlink
    swapped = False

    def swap_parent_then_unlink(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path).name == quarantined.name:
            swapped = True
            quarantine.rename(detached)
            os.symlink(outside, quarantine)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", swap_parent_then_unlink)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert swapped is True
    assert outside_candidate.exists()


def test_s3_generation_digest_mismatch_aborts_without_deletion(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    candidate.write_bytes(candidate.read_bytes() + b"changed")
    _age(candidate, now=now, seconds=3601)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()


def test_s4_scan_oserror_aborts_before_any_quarantine(tmp_path, monkeypatch):
    import mission_persistence.gc as gc_module
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)
    original_scan = gc_module._scan_directory

    def fail_generations(path):
        if path == repository / "generations":
            raise OSError("injected scan failure")
        return original_scan(path)

    monkeypatch.setattr(gc_module, "_scan_directory", fail_generations)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()
    assert not _quarantine_path(repository, digest).exists()


def test_s5_unreadable_archive_pointer_fails_closed(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    bundle = _write_archive_pointer(repository, tmp_path, digest)
    (bundle / "current.json").chmod(0)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()


def test_s5_symlinked_archive_bundle_fails_closed_before_quarantine(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    bundle = _write_archive_pointer(repository, tmp_path, digest)
    target = tmp_path / "archive-bundle-target"
    bundle.replace(target)
    os.symlink(target, bundle)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    candidate = _generation_path(repository, digest)
    _age(candidate, now=now, seconds=3601)

    with pytest.raises(FencedCommitError):
        local.collect(_destructive_policy())

    assert candidate.exists()
    assert not _quarantine_path(repository, digest).exists()


def test_open_recovery_nil_generation_ref_fails_closed(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    candidate = _generation_path(repository, fixture["results"][0].state_generation_digest)
    _age(candidate, now=clock.current, seconds=3601)
    prepared_dir = repository / "transactions" / "prepared"
    (prepared_dir / ("a" * 32 + ".json")).write_text(
        json.dumps(
            {
                "schema": "mission-prepare/1",
                "generation": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FencedCommitError) as excinfo:
        local.collect(_destructive_policy())

    assert excinfo.value.code == "gc-root-ambiguous"
    # A malformed prepare record must fail closed before GC can rely on the
    # open-recovery root, and the candidate must remain on disk.
    assert candidate.exists()


def test_s6_interrupted_quarantine_is_idempotently_recoverable(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=3601)
    fired = False

    def interrupt(point):
        nonlocal fired
        if point == "after-gc-quarantine:" + digest.removeprefix("sha256:") and not fired:
            fired = True
            raise RuntimeError("injected quarantine interruption")

    local.fault_injector = interrupt
    with pytest.raises(RuntimeError, match="quarantine interruption"):
        local.collect(_destructive_policy())
    local.fault_injector = None

    local.collect(_destructive_policy())

    assert not _generation_path(repository, digest).exists()
    assert _quarantine_path(repository, digest).exists()


def test_s7_interrupted_purge_is_idempotently_recoverable(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=3601)
    local.collect(_destructive_policy())
    quarantined = _quarantine_path(repository, digest)
    purge_time = datetime.fromtimestamp(
        quarantined.stat().st_ctime, timezone.utc
    ) + timedelta(seconds=86401)
    local.clock = lambda: purge_time
    fired = False

    def interrupt(point):
        nonlocal fired
        if point == "after-gc-purge:" + digest.removeprefix("sha256:") and not fired:
            fired = True
            raise RuntimeError("injected purge interruption")

    local.fault_injector = interrupt
    with pytest.raises(RuntimeError, match="purge interruption"):
        local.collect(_destructive_policy())
    local.fault_injector = None

    report = local.collect(_destructive_policy())

    assert not quarantined.exists()
    assert report.purged == ()


def test_open_recovery_root_is_never_purged_from_quarantine(tmp_path):
    from mission_persistence.gc import RetentionPolicy

    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    third_bytes = fixture["state_bytes"][2]
    clock.current += timedelta(seconds=1)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "prepared-quarantine-cli",
        third_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="done",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-prepared-quarantine-root",
            lease_id="fixture-lease",
            argv=("set", "phase=done"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    digest = prepared.staged.generation_digest

    def interrupt(point):
        if point == "after-generation-publish":
            raise RuntimeError("leave an open recovery root")

    local.fault_injector = interrupt
    with pytest.raises(RuntimeError, match="open recovery root"):
        local.commit(prepared, prepared.precondition)
    local.fault_injector = None
    generation = _generation_path(repository, digest)
    quarantined = _quarantine_path(repository, digest)
    generation.replace(quarantined)
    purge_time = datetime.fromtimestamp(
        quarantined.stat().st_ctime, timezone.utc
    ) + timedelta(seconds=86401)
    local.clock = lambda: purge_time

    report = local.collect(
        RetentionPolicy(dry_run=False, destructive=True)
    )

    # The open recovery root is still referenced, so purge must skip it even
    # after the quarantine grace period has elapsed.
    assert report.purged == ()
    assert quarantined.exists()


def test_d2_dry_run_false_without_explicit_destructive_flag_is_rejected(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository
    from mission_persistence.gc import RetentionPolicy

    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state")

    with pytest.raises(FencedCommitError):
        local.collect(RetentionPolicy(dry_run=False))


def test_m1_collect_never_changes_mission_state_bytes(tmp_path):
    fixture = _three_commits(tmp_path)
    local = fixture["local"]
    repository = fixture["repository"]
    clock = fixture["clock"]
    first = fixture["results"][0]
    before = local.read("test").state_bytes
    _age(_generation_path(repository, first.state_generation_digest), now=clock.current, seconds=3601)

    local.collect(_destructive_policy())

    assert local.read("test").state_bytes == before


@pytest.mark.parametrize(
    "fault_point",
    (
        "after-stage",
        "after-prepare",
        "after-generation-publish",
        "after-commit-publish",
        "before-head-replace",
        "after-head-replace",
        "after-operation-publish",
        "after-lineage-verify",
        "after-resolution-marker",
        "after-finalize",
    ),
    ids=(
        "stage",
        "prepare",
        "generation",
        "commit",
        "before-head",
        "head",
        "operation",
        "lineage",
        "resolution",
        "finalize",
    ),
)
def test_m2_u3_crash_recovery_converges_with_gc_interleaving(tmp_path, fault_point):
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.gc import RetentionPolicy

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / ("gc-interleaving-" + fault_point),
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    target_path = tmp_path / ("gc-interleaving-" + fault_point + ".json")
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fault_point=fault_point,
    )
    assert killed.returncode == 91, killed.stderr

    if fault_point == "after-head-replace":
        gc_digest = _publish_unreferenced_generation(repository, tmp_path)
        _age(_generation_path(repository, gc_digest), now=clock.current, seconds=3601)
        fired = False

        def interrupt(point):
            nonlocal fired
            if point == "after-gc-quarantine:" + gc_digest.removeprefix("sha256:") and not fired:
                fired = True
                raise RuntimeError("injected gc interruption")

        local.fault_injector = interrupt
        with pytest.raises(RuntimeError, match="gc interruption"):
            local.collect(_destructive_policy())
        local.fault_injector = None

        assert not _generation_path(repository, gc_digest).exists()
        assert _quarantine_path(repository, gc_digest).exists()
    else:
        generations_before = {
            path.name: path.read_bytes() for path in (repository / "generations").iterdir()
        }
        try:
            local.collect(RetentionPolicy())
        except FencedCommitError:
            # A head-swap temp is an intentionally incomplete scan. GC must block,
            # preserve every generation, and leave U3 to classify the transaction.
            assert fault_point == "before-head-replace"
            assert generations_before == {
                path.name: path.read_bytes()
                for path in (repository / "generations").iterdir()
            }
    first = local.recover("test")
    second = local.recover("test")

    assert first == second
    assert first.ready is True
    assert local.read("test").state_bytes in {base_bytes, target_bytes}


@pytest.mark.parametrize(
    ("age_seconds", "expected_purged"),
    ((PURGE_GRACE_SECONDS - 1, False), (PURGE_GRACE_SECONDS + 1, True)),
    ids=("purge-grace-minus-one", "purge-grace-plus-one"),
)
def test_b2_purge_grace_boundary_pair(tmp_path, age_seconds, expected_purged):
    from mission_persistence.fenced_commit import LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    local.recover("test")
    digest = _publish_unreferenced_generation(repository, tmp_path)
    now = datetime.now(timezone.utc)
    local.clock = lambda: now
    _age(_generation_path(repository, digest), now=now, seconds=3601)
    local.collect(_destructive_policy())
    quarantined = _quarantine_path(repository, digest)
    local.clock = lambda: datetime.fromtimestamp(
        quarantined.stat().st_ctime, timezone.utc
    ) + timedelta(seconds=age_seconds)

    report = local.collect(_destructive_policy())

    assert (digest in report.purged) is expected_purged
    assert quarantined.exists() is (not expected_purged)
