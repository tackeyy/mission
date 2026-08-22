"""Issue #119: aggregate reviewer JSON into deterministic scoring JSON."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import stat
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("mission_state_issue119_atomic", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _archive_bytes(state_dir):
    archive = state_dir / "archive"
    return {path.name: path.read_bytes() for path in archive.iterdir()} if archive.exists() else {}


def _aggregate_args(review, out):
    return argparse.Namespace(
        iteration=1, input=[str(review)], input_refs=[], out=str(out), json=True,
        min_reviewers=None, reviewer_windows=[], base_sha=None, head_sha=None,
        record_outcome=True, event_id="aggregate-atomic", root_event_id="aggregate-root",
        attempt=1, retry_of=None,
    )


def _review(tmp_path, name, *, perspective="A", iteration=1, scores=None, findings=None, same_score_note=None, learning=False):
    payload = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": scores if scores is not None else {
            "mission_achievement": 4.6,
            "accuracy": 4.4,
            "completeness": 4.2,
            "usability": 4.0,
        },
        "findings": findings if findings is not None else [],
        "same_score_note": same_score_note,
        "notes": f"{perspective} review",
    }
    if learning:
        payload["learning_schema"] = "mission-review-learning/1"
        payload["findings"] = [{
            "id": f"{perspective}-1", "severity": "Medium", "axis": "accuracy",
            "summary": "Boundary missing", "evidence": "bounded evidence", "recommendation": "validate it",
            "cause": "Validation was omitted", "general_fix_rule": "Validate every boundary",
            "weak_phase": "execution",
        }]
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _reviewer_windows(*perspectives):
    args = []
    for perspective in perspectives:
        args.extend((
            "--reviewer-window",
            f"{perspective}=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
        ))
    return args


def test_aggregate_reviews_writes_scoring_json_and_evidence(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 4.4,
        "accuracy": 4.2,
        "completeness": 4.0,
        "usability": 3.8,
    })
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--out", str(out), "--json", *_reviewer_windows("A", "B"), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    payload = _load(out)
    assert result["out"] == str(out)
    assert payload["items"] == {
        "mission_achievement": 4.5,
        "accuracy": 4.3,
        "completeness": 4.1,
        "usability": 3.9,
    }
    assert payload["review_agreement"] == 5.0
    assert payload["open_high"] == 0
    assert (state_dir.parent / payload["findings_evidence_path"]).exists()


def test_aggregate_output_io_failure_keeps_state_archive_and_outcome_consistent(
    state_dir, run_cli, tmp_path,
):
    review = _review(tmp_path, "io-review.json", perspective="quality")
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_bytes(b"sentinel")
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--out", str(blocked_parent / "score.json"), "--json",
        "--event-id", "aggregate-io", cwd=state_dir.parent,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "internal-error"
    assert payload["outcome"]["command"] == "aggregate-reviews"
    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    assert not (blocked_parent / "score.json").exists()


def test_aggregate_state_publish_failure_rolls_back_new_archive_and_output(
    state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "state-failure.json", perspective="quality")
    out = tmp_path / "score.json"
    previous_out = b"previous-score\n"
    out.write_bytes(previous_out)
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    original_write = module.atomic_write_json

    def fail_state_publish(path, data, **kwargs):
        if path == state_path:
            assert out.read_bytes() != previous_out
            assert _archive_bytes(state_dir) != archive_before
            raise OSError("simulated aggregate state publish failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_state_publish)

    with pytest.raises(OSError, match="simulated aggregate state publish failure"):
        module.cmd_aggregate_reviews(_aggregate_args(review, out))

    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    assert out.read_bytes() == previous_out
    assert not list(state_dir.rglob(".*.tmp"))


def test_aggregate_state_failure_preserves_concurrently_published_archive(
    state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "concurrent.json", perspective="quality")
    first_out = tmp_path / "first-score.json"
    module.cmd_aggregate_reviews(_aggregate_args(review, first_out))
    archive = state_dir / "archive"
    published_path = next(archive.iterdir())
    published_name = published_path.name
    published_content = published_path.read_bytes()
    published_path.unlink()
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    second_out = tmp_path / "second-score.json"
    original_publish = module._publish_review_archive_transaction
    original_write = module.atomic_write_json

    def concurrent_publish(cwd, name, content):
        assert name == published_name
        assert content == published_content
        (archive / name).write_bytes(content)
        return original_publish(cwd, name, content)

    def fail_state_publish(path, data, **kwargs):
        if path == state_path:
            raise OSError("simulated aggregate state publish failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "_publish_review_archive_transaction", concurrent_publish)
    monkeypatch.setattr(module, "atomic_write_json", fail_state_publish)

    with pytest.raises(OSError, match="simulated aggregate state publish failure"):
        module.cmd_aggregate_reviews(_aggregate_args(review, second_out))

    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == {published_name: published_content}
    assert not second_out.exists()
    assert not list(state_dir.rglob(".*.tmp"))


def test_aggregate_rollback_does_not_unlink_replaced_output(
    state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "output-swap.json", perspective="quality")
    out = tmp_path / "score.json"
    detached = tmp_path / "detached-score.json"
    external = b"external-writer\n"
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    original_write = module.atomic_write_json

    def fail_after_output_swap(path, data, **kwargs):
        if path == state_path:
            out.rename(detached)
            out.write_bytes(external)
            raise OSError("simulated state failure after output replacement")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_after_output_swap)

    with pytest.raises(OSError, match="simulated state failure after output replacement"):
        module.cmd_aggregate_reviews(_aggregate_args(review, out))

    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    assert out.read_bytes() == external
    assert detached.exists()


def test_aggregate_rollback_does_not_publish_into_replaced_output_parent(
    state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "output-parent-swap.json", perspective="quality")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    out = output_parent / "score.json"
    detached_parent = tmp_path / "detached-output"
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    original_write = module.atomic_write_json

    def fail_after_parent_swap(path, data, **kwargs):
        if path == state_path:
            output_parent.rename(detached_parent)
            output_parent.mkdir()
            (output_parent / "sentinel").write_bytes(b"replacement-parent")
            raise OSError("simulated state failure after output parent replacement")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_after_parent_swap)

    with pytest.raises(OSError, match="simulated state failure after output parent replacement"):
        module.cmd_aggregate_reviews(_aggregate_args(review, out))

    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    assert (output_parent / "sentinel").read_bytes() == b"replacement-parent"
    assert not (output_parent / "score.json").exists()
    assert (detached_parent / "score.json").is_file()


def test_output_publisher_post_replace_stat_failure_restores_previous_bytes(
    tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    previous = b"previous-output\n"
    replacement = b"replacement-output\n"
    out.write_bytes(previous)
    original_replace = module.os.replace
    original_stat = module.os.stat
    replaced = rejected = False

    def observe_replace(src, dst, **kwargs):
        nonlocal replaced
        result = original_replace(src, dst, **kwargs)
        if dst == out.name:
            replaced = True
        return result

    def fail_first_post_replace_stat(path, *args, **kwargs):
        nonlocal rejected
        if replaced and path == out.name and not rejected:
            rejected = True
            raise OSError("simulated post-replace stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "replace", observe_replace)
    monkeypatch.setattr(module.os, "stat", fail_first_post_replace_stat)

    with pytest.raises(ValueError, match="publish failed"):
        module._publish_output_transaction(out, replacement)

    assert rejected is True
    assert out.read_bytes() == previous
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


@pytest.mark.parametrize("preexisting", [False, True], ids=["new", "existing"])
def test_output_publisher_return_boundary_failure_restores_prior_state(
    preexisting, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    previous = b"previous-output\n"
    if preexisting:
        out.write_bytes(previous)
    original_finish = module._finish_published_file
    rejected = False

    def fail_after_verify(published):
        nonlocal rejected
        result = original_finish(published)
        if not rejected:
            rejected = True
            raise OSError("simulated output return-boundary failure")
        return result

    monkeypatch.setattr(module, "_finish_published_file", fail_after_verify)

    with pytest.raises(ValueError, match="publish failed"):
        module._publish_output_transaction(out, b"replacement-output\n")

    assert rejected is True
    if preexisting:
        assert out.read_bytes() == previous
    else:
        assert not out.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


def test_output_publisher_does_not_rollback_competitor_hardlink_of_its_temp(
    tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    content = b"competitor-published-output\n"
    original_link = module.os.link
    competed = False

    def publish_same_temp_first(src, dst, **kwargs):
        nonlocal competed
        if dst == out.name and not competed:
            competed = True
            original_link(src, dst, **kwargs)
        return original_link(src, dst, **kwargs)

    monkeypatch.setattr(module.os, "link", publish_same_temp_first)

    with pytest.raises(ValueError, match="appeared during publish"):
        module._publish_output_transaction(out, content)

    assert competed is True
    assert out.read_bytes() == content
    assert out.stat().st_nlink == 1
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


@pytest.mark.parametrize(
    "fault", ["temp-write", "temp-fsync", "link", "replace", "directory-fsync"],
)
def test_output_rollback_fault_never_loses_the_only_recoverable_copy(
    fault, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    state = tmp_path / "state.json"
    previous = b"previous-output\n"
    replacement = b"replacement-output\n"
    state.write_bytes(b"state-before\n")
    out.write_bytes(previous)
    published = module._publish_output_transaction(out, replacement)
    original_write_temp = module._write_temp_at
    original_fsync = module.os.fsync
    faulted = False
    directory_fsyncs = 0

    def fail_previous_temp(directory_fd, name, content):
        nonlocal faulted
        if content == previous and not faulted:
            faulted = True
            raise OSError("simulated previous-output temp write failure")
        return original_write_temp(directory_fd, name, content)

    def fail_selected_fsync(fd):
        nonlocal directory_fsyncs, faulted
        is_directory = stat.S_ISDIR(module.os.fstat(fd).st_mode)
        if is_directory:
            directory_fsyncs += 1
        if not faulted and (
            (fault == "temp-fsync" and not is_directory)
            or (fault == "directory-fsync" and is_directory and directory_fsyncs == 2)
        ):
            faulted = True
            raise OSError(f"simulated {fault} failure")
        return original_fsync(fd)

    def fail_restore_link(*args, **kwargs):
        nonlocal faulted
        faulted = True
        raise OSError("simulated restore link failure")

    def fail_restore_replace(*args, **kwargs):
        nonlocal faulted
        faulted = True
        raise OSError("simulated restore replace failure")

    if fault == "temp-write":
        monkeypatch.setattr(module, "_write_temp_at", fail_previous_temp)
    elif fault in {"temp-fsync", "directory-fsync"}:
        monkeypatch.setattr(module.os, "fsync", fail_selected_fsync)
    elif fault == "link":
        monkeypatch.setattr(module.os, "link", fail_restore_link)
    else:
        monkeypatch.setattr(module.os, "replace", fail_restore_replace)

    rollback_error = None
    if fault == "link":
        module._rollback_published_file(published)
        assert faulted is False
    elif fault == "replace":
        with pytest.raises(module.PublishedRollbackRecoveryError) as stopped:
            module._rollback_published_file(published)
        rollback_error = stopped.value
        assert faulted is True
    else:
        with pytest.raises(ValueError, match="rollback failed"):
            module._rollback_published_file(published)
        assert faulted is True

    contents = {
        path.name: path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file() and path != state
    }
    assert state.read_bytes() == b"state-before\n"
    if fault in {"temp-write", "temp-fsync"}:
        assert out.read_bytes() == replacement
    elif fault == "link":
        assert out.read_bytes() == previous
        assert contents == {out.name: previous}
    elif fault == "replace":
        assert rollback_error is not None
        recovery_ref = rollback_error.recovery_ref
        assert set(recovery_ref) == {"basename", "digest", "size"}
        recovery = tmp_path / recovery_ref["basename"]
        recovery_stat = recovery.stat()
        assert stat.S_ISREG(recovery_stat.st_mode)
        assert stat.S_IMODE(recovery_stat.st_mode) == 0o600
        assert recovery_stat.st_nlink == 1
        assert recovery.read_bytes() == previous
        assert recovery_ref["digest"] == "sha256:" + hashlib.sha256(previous).hexdigest()
        assert recovery_ref["size"] == len(previous)
        assert not list(tmp_path.glob(".score.json.restore.*.tmp"))
        assert previous in contents.values()
        assert replacement in contents.values()
    else:
        assert out.read_bytes() == previous
        assert replacement in contents.values()


@pytest.mark.parametrize("mutation_phase", ["pre-replace", "post-replace"])
def test_output_rollback_rejects_same_inode_restore_content_mutation(
    mutation_phase, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    state = tmp_path / "state.json"
    previous = b"previous-output\n"
    replacement = b"replacement-output\n"
    mutated = b"malicious-output"
    assert len(mutated) == len(previous)
    out.write_bytes(previous)
    state.write_bytes(b"state-before\n")
    published = module._publish_output_transaction(out, replacement)
    original_write_temp = module._write_temp_at
    original_link = module.os.link
    original_replace = module.os.replace
    mutated_restore = False

    def overwrite_same_inode(name, *, directory_fd):
        fd = module.os.open(
            name,
            module.os.O_WRONLY | module.os.O_TRUNC | getattr(module.os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            assert module.os.write(fd, mutated) == len(mutated)
            module.os.fsync(fd)
        finally:
            module.os.close(fd)

    def mutate_returned_restore(directory_fd, name, content):
        nonlocal mutated_restore
        result = original_write_temp(directory_fd, name, content)
        if mutation_phase == "pre-replace" and content == previous:
            overwrite_same_inode(result[0], directory_fd=directory_fd)
            mutated_restore = True
        return result

    def mutate_after_link(src, dst, **kwargs):
        nonlocal mutated_restore
        result = original_link(src, dst, **kwargs)
        if mutation_phase == "post-replace" and ".restore." in str(src):
            overwrite_same_inode(dst, directory_fd=kwargs["dst_dir_fd"])
            mutated_restore = True
        return result

    def mutate_after_replace(src, dst, **kwargs):
        nonlocal mutated_restore
        result = original_replace(src, dst, **kwargs)
        if mutation_phase == "post-replace" and ".restore." in str(src):
            overwrite_same_inode(dst, directory_fd=kwargs["dst_dir_fd"])
            mutated_restore = True
        return result

    monkeypatch.setattr(module, "_write_temp_at", mutate_returned_restore)
    monkeypatch.setattr(module.os, "link", mutate_after_link)
    monkeypatch.setattr(module.os, "replace", mutate_after_replace)

    with pytest.raises(ValueError, match="restore changed"):
        module._rollback_published_file(published)

    assert mutated_restore is True
    assert state.read_bytes() == b"state-before\n"
    assert out.read_bytes() == replacement
    assert mutated not in {
        path.read_bytes() for path in tmp_path.iterdir() if path.is_file() and path != state
    }


@pytest.mark.parametrize("publisher", ["output-new", "output-existing", "archive"])
def test_publisher_recovers_own_target_when_base_exception_follows_syscall_success(
    publisher, tmp_path, monkeypatch,
):
    module = _load_mission_state()

    class SimulatedAsyncBoundary(BaseException):
        pass

    original_link = module.os.link
    original_replace = module.os.replace
    interrupted = False

    def interrupt_after_link(src, dst, **kwargs):
        nonlocal interrupted
        result = original_link(src, dst, **kwargs)
        if not interrupted:
            interrupted = True
            raise SimulatedAsyncBoundary("simulated signal after link success")
        return result

    def interrupt_after_replace(src, dst, **kwargs):
        nonlocal interrupted
        result = original_replace(src, dst, **kwargs)
        if not interrupted:
            interrupted = True
            raise SimulatedAsyncBoundary("simulated signal after replace success")
        return result

    if publisher == "archive":
        cwd = tmp_path / "project"
        archive = cwd / ".mission-state" / "archive"
        archive.mkdir(parents=True)
        monkeypatch.setattr(module.os, "link", interrupt_after_link)
        with pytest.raises(SimulatedAsyncBoundary):
            module._publish_review_archive_transaction(cwd, "review.json", b"review\n")
        assert not (archive / "review.json").exists()
        residue_parent = archive
    else:
        out = tmp_path / "score.json"
        previous = b"previous-output\n"
        if publisher == "output-existing":
            out.write_bytes(previous)
            monkeypatch.setattr(module.os, "replace", interrupt_after_replace)
        else:
            monkeypatch.setattr(module.os, "link", interrupt_after_link)
        with pytest.raises(SimulatedAsyncBoundary):
            module._publish_output_transaction(out, b"replacement-output\n")
        if publisher == "output-existing":
            assert out.read_bytes() == previous
        else:
            assert not out.exists()
        residue_parent = tmp_path

    assert interrupted is True
    assert not list(residue_parent.glob(".*.tmp"))
    assert not list(residue_parent.glob(".*.rollback"))


def test_real_sigint_cannot_escape_publish_ownership_rollback(tmp_path):
    if not hasattr(os, "fork"):
        pytest.skip("requires a POSIX child process")
    pid = os.fork()
    if pid == 0:
        try:
            module = _load_mission_state()
            out = tmp_path / "signal-output.json"
            original_link = module.os.link

            def signal_after_link(src, dst, **kwargs):
                result = original_link(src, dst, **kwargs)
                os.kill(os.getpid(), signal.SIGINT)
                return result

            module.os.link = signal_after_link
            try:
                module._publish_output_transaction(out, b"signal-boundary\n")
            except KeyboardInterrupt:
                pass
            else:
                os._exit(10)
            if out.exists() or list(tmp_path.glob(".*.tmp")):
                os._exit(11)
            os._exit(0)
        except BaseException:
            os._exit(12)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0


@pytest.mark.parametrize("publisher", ["output", "archive"])
def test_pending_sigint_cannot_replace_fileexists_conflict_ownership(
    publisher, tmp_path,
):
    if not hasattr(os, "fork"):
        pytest.skip("requires a POSIX child process")
    pid = os.fork()
    if pid == 0:
        try:
            module = _load_mission_state()
            content = b"competitor-same-temp\n"
            if publisher == "archive":
                cwd = tmp_path / "project"
                target = cwd / ".mission-state" / "archive" / "review.json"
                target.parent.mkdir(parents=True)
            else:
                cwd = tmp_path
                target = tmp_path / "score.json"
            original_link = module.os.link

            def competitor_then_pending_signal(src, dst, **kwargs):
                original_link(src, dst, **kwargs)
                os.kill(os.getpid(), signal.SIGINT)
                raise FileExistsError("simulated competitor publish")

            module.os.link = competitor_then_pending_signal
            try:
                if publisher == "archive":
                    module._publish_review_archive_transaction(cwd, target.name, content)
                else:
                    module._publish_output_transaction(target, content)
            except KeyboardInterrupt:
                pass
            else:
                os._exit(20)
            if not target.exists() or target.read_bytes() != content:
                os._exit(21)
            if target.stat().st_nlink != 1 or list(target.parent.glob(".*.tmp")):
                os._exit(22)
            os._exit(0)
        except BaseException:
            os._exit(23)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0


def test_main_internal_error_envelope_exposes_recovery_ref_without_sidecar_locator(
    state_dir, tmp_path, monkeypatch, capsys,
):
    module = _load_mission_state()
    review = _review(tmp_path, "recovery-envelope-review.json", perspective="quality")
    out = tmp_path / "score.json"
    previous = b"previous-output\n"
    out.write_bytes(previous)
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    original_write = module.atomic_write_json
    original_replace = module.os.replace

    def fail_state_publish(path, data, **kwargs):
        if path == state_path:
            raise OSError("simulated aggregate state publish failure")
        return original_write(path, data, **kwargs)

    def fail_restore_replace(src, dst, **kwargs):
        if ".restore." in str(src):
            raise OSError("simulated aggregate restore failure")
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_state_publish)
    monkeypatch.setattr(module.os, "replace", fail_restore_replace)
    args = _aggregate_args(review, out)
    args.cmd = "aggregate-reviews"
    args.func = module.cmd_aggregate_reviews
    args.command_outcome_tracking = True
    args.command_outcome_emitted = False
    parser = argparse.Namespace(parse_args=lambda: args)
    monkeypatch.setattr(module, "_build_parser", lambda: parser)
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")

    with pytest.raises(SystemExit) as stopped:
        module.main()

    assert stopped.value.code == 1
    envelope = json.loads(capsys.readouterr().out)
    recovery_ref = envelope["recovery_ref"]
    recovery = tmp_path / recovery_ref["basename"]
    assert recovery.read_bytes() == previous
    assert recovery_ref["digest"] == "sha256:" + hashlib.sha256(previous).hexdigest()
    assert recovery_ref["size"] == len(previous)
    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    record = json.loads(sidecar.read_text(encoding="utf-8"))["records"][-1]
    assert "recovery_ref" not in record
    assert "path" not in json.dumps(record)


@pytest.mark.parametrize("alias_kind", ["symlink-ancestor", "relative-parent"])
def test_aggregate_rejects_alias_of_evidence_target_before_output_publish(
    alias_kind, state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "alias-target.json", perspective="quality")
    module.cmd_aggregate_reviews(_aggregate_args(review, tmp_path / "first-score.json"))
    archive = state_dir / "archive"
    evidence = next(archive.glob("*-reviews-*.json"))
    if alias_kind == "symlink-ancestor":
        alias = tmp_path / "archive-alias"
        alias.symlink_to(archive, target_is_directory=True)
        alias_out = alias / evidence.name
    else:
        alias_out = archive / ".." / "archive" / evidence.name
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    original_publish = module._publish_output_transaction
    publish_calls = 0

    def counted_publish(path, content, **kwargs):
        nonlocal publish_calls
        publish_calls += 1
        return original_publish(path, content, **kwargs)

    monkeypatch.setattr(module, "_publish_output_transaction", counted_publish)

    with pytest.raises(module.CommandOutcomeExit) as stopped:
        module.cmd_aggregate_reviews(_aggregate_args(review, alias_out))

    assert stopped.value.code == 2
    assert publish_calls == 0
    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before


@pytest.mark.skipif(
    Path("/tmp").resolve() == Path("/tmp"),
    reason="system temporary directory has no canonical alias",
)
def test_publish_target_identity_recognizes_system_temporary_alias():
    module = _load_mission_state()

    assert module._same_publish_target(
        Path("/tmp") / "mission-output.json",
        Path("/tmp").resolve() / "mission-output.json",
    )


def test_aggregate_rechecks_opened_output_directory_against_archive_identity(
    state_dir, tmp_path, monkeypatch,
):
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "opened-alias.json", perspective="quality")
    module.cmd_aggregate_reviews(_aggregate_args(review, tmp_path / "first-score.json"))
    archive = state_dir / "archive"
    evidence = next(archive.glob("*-reviews-*.json"))
    alias = tmp_path / "late-alias"
    alias.symlink_to(archive, target_is_directory=True)
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    temp_writes = 0
    original_temp_write = module._write_temp_at

    def counted_temp_write(*args, **kwargs):
        nonlocal temp_writes
        temp_writes += 1
        return original_temp_write(*args, **kwargs)

    monkeypatch.setattr(module, "_same_publish_target", lambda *_: False)
    monkeypatch.setattr(module, "_write_temp_at", counted_temp_write)

    with pytest.raises(module.CommandOutcomeExit) as stopped:
        module.cmd_aggregate_reviews(_aggregate_args(review, alias / evidence.name))

    assert stopped.value.code == 2
    assert temp_writes == 0
    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before


def test_aggregate_reviews_is_deterministic(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    out1 = tmp_path / "one.json"
    out2 = tmp_path / "two.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out1), cwd=state_dir.parent, check=True)
    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out2), cwd=state_dir.parent, check=True)

    assert _load(out1) == _load(out2)


def test_aggregate_reviews_caps_scores_per_reviewer_findings(state_dir, run_cli, tmp_path):
    finding = {
        "id": "A-1",
        "severity": "High",
        "axis": "accuracy",
        "summary": "Bug remains",
        "evidence": "file.py:1 `bad()`",
        "recommendation": "Fix it",
    }
    a = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 5.0,
        "accuracy": 5.0,
        "completeness": 5.0,
        "usability": 5.0,
    }, findings=[finding], same_score_note="All axes independently checked")
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    payload = _load(out)
    assert payload["items"]["accuracy"] == 3.0
    assert payload["items"]["mission_achievement"] == 5.0
    assert payload["open_high"] == 1


def test_aggregate_reviews_uses_findings_only_reviewer_without_scores(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    d = _review(tmp_path, "d.json", perspective="D", scores=None, findings=[{
        "id": "D-1",
        "severity": "Low",
        "axis": "completeness",
        "summary": "Planning note",
        "evidence": "",
        "recommendation": "Clarify next plan",
    }])
    payload = _load(d)
    payload["scores"] = None
    d.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(d),
                "--out", str(out), *_reviewer_windows("A", "D"), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    payload = _load(out)
    assert "reviewer_consensus" not in payload["items"]
    assert payload["review_agreement"] is None
    assert "1 scoring reviewer(s), 1 findings-only reviewer(s)" in payload["notes"]


def test_aggregate_reviews_counts_high_from_findings_only_reviewer(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    d = _review(tmp_path, "d.json", perspective="D", scores=None, findings=[{
        "id": "D-1",
        "severity": "High",
        "axis": "accuracy",
        "summary": "Blocking finding from non-scoring reviewer",
        "evidence": "file.py:1 `bad()`",
        "recommendation": "Fix before pass",
    }])
    payload = _load(d)
    payload["scores"] = None
    d.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "scoring.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(d),
            "--out", str(out), *_reviewer_windows("A", "D"), cwd=state_dir.parent, check=True)

    assert _load(out)["open_high"] == 1


def test_aggregate_reviews_consensus_score_boundaries(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 5.0, "accuracy": 4.8, "completeness": 4.8, "usability": 4.8,
    })
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 3.4, "accuracy": 4.8, "completeness": 4.8, "usability": 4.8,
    })
    out = tmp_path / "scoring.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out), *_reviewer_windows("A", "B"), cwd=state_dir.parent, check=True)

    payload = _load(out)
    assert payload["review_agreement"] == 2.0
    assert payload["agreement_detail"]["mission_achievement"]["delta"] == 1.6


def test_aggregate_reviews_output_can_be_pushed(state_dir, run_cli, read_state, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    out = tmp_path / "scoring.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out), cwd=state_dir.parent, check=True)
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    entry = read_state(state_dir)["score_history"][0]
    assert entry["score_source"] == "scoring-json"
    assert entry["items"]["accuracy"] == 4.4
    assert entry["review_agreement"] is None


def test_learning_review_materializes_digest_only_failure_ledger(state_dir, run_cli, read_state, tmp_path):
    review = _review(tmp_path, "learning.json", perspective="A", learning=True)
    out = tmp_path / "scoring-learning.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(out), cwd=state_dir.parent, check=True)
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    ledger = read_state(state_dir)["failure_ledger"]
    pattern = ledger["patterns"][0]
    assert pattern["weak_phase"] == "execution"
    assert pattern["iterations"] == [1]
    serialized = json.dumps(ledger)
    assert "bounded evidence" not in serialized
    assert "Validation was omitted" not in serialized
    assert pattern["examples"][0]["review_aggregate_digest"].startswith("sha256:")


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda p: p.pop("schema"), "schema"),
        (lambda p: p.update(iteration=2), "iteration"),
        (lambda p: p.pop("scores"), "scores field is required"),
        (lambda p: p.update(scores={"mission_achievement": 4.0}), "scores"),
        (lambda p: p["scores"].update(accuracy=0.5, mission_achievement=0.5, completeness=0.5, usability=0.5), "0-1"),
        (lambda p: p["findings"].append({"id": "A-1", "severity": "High", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"}), "evidence"),
        (lambda p: p["findings"].append({"id": "A-1", "severity": "Medium", "axis": "wrong", "summary": "x", "evidence": "e", "recommendation": "y"}), "axis"),
        (lambda p: p["findings"].extend([
            {"id": "A-1", "severity": "Low", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"},
            {"id": "A-1", "severity": "Low", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"},
        ]), "duplicate"),
        (lambda p: p.update(scores={k: 4.0 for k in ("mission_achievement", "accuracy", "completeness", "usability")}, same_score_note=None), "same_score_note"),
    ],
)
def test_aggregate_reviews_rejects_invalid_review_json(state_dir, run_cli, tmp_path, mutate, expected):
    src = _review(tmp_path, "bad.json", perspective="A")
    payload = _load(src)
    mutate(payload)
    src.write_text(json.dumps(payload), encoding="utf-8")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert expected in r.stderr


def test_aggregate_reviews_rejects_findings_only_inputs(state_dir, run_cli, tmp_path):
    src = _review(tmp_path, "d.json", perspective="D")
    payload = _load(src)
    payload["scores"] = None
    src.write_text(json.dumps(payload), encoding="utf-8")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "採点対象 reviewer" in r.stderr


def test_aggregate_reviews_rejects_overall_impression_same_score(state_dir, run_cli, tmp_path):
    src = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
    }, same_score_note="全体印象で同じ点にした")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "全採点 reviewer" in r.stderr


# ===== #612: lease-first (公開前に lease を検証する) =====


def _set_foreign_lease(state_dir):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "owner_session_id": "foreign",
        "lease_id": "foreign-lease",
        "fencing_epoch": 7,
        "lease_expires_at": "2099-01-01T00:00:00Z",
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def test_aggregate_reviews_rejects_foreign_lease_before_publishing_any_evidence(
    state_dir, run_cli, tmp_path,
):
    """#612: foreign lease は archive / 出力を一度も公開せずに拒否される.

    注意: この end-to-end テスト単体では「公開前拒否」と「公開後 rollback による
    回収」を区別できない (rollback 成功時も bytes は不変になる)。契約の核心は
    下の probe テストが固定しており、両方を維持すること。
    """
    review = _review(tmp_path, "lease-review.json", perspective="quality")
    out = tmp_path / "score.json"
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--out", str(out), "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "lease" in result.stderr.lower()
    assert state_path.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    assert not out.exists()


def test_aggregate_reviews_does_not_publish_before_foreign_lease_rejection(
    state_dir, tmp_path, monkeypatch, capsys,
):
    """#612: rollback による回収ではなく、公開関数が一度も呼ばれないこと。

    #475 の契約は「lease/CAS 検証前に外部可視 file を公開しない」であり、
    「公開しても最後に回収する」ではない。公開関数へ到達した時点で違反。
    """
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "lease-probe.json", perspective="quality")
    out = tmp_path / "score.json"
    _set_foreign_lease(state_dir)

    def record_review_publish(*args, **kwargs):
        raise AssertionError("aggregate-reviews must reject foreign lease before archive publish")

    def record_output_publish(*args, **kwargs):
        raise AssertionError("aggregate-reviews must reject foreign lease before output publish")

    monkeypatch.setattr(module, "_publish_review_archive_transaction", record_review_publish)
    monkeypatch.setattr(module, "_publish_output_transaction", record_output_publish)

    with pytest.raises(SystemExit) as stopped:
        module.cmd_aggregate_reviews(_aggregate_args(review, out))

    captured = capsys.readouterr()
    assert stopped.value.code == 2
    assert "lease" in captured.err.lower()
    assert not out.exists()
