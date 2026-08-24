"""Issue #655: explicitly start a new mission in one terminal session."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


def _authoritative_state(root: Path, session_id: str) -> dict:
    from mission_persistence.authoritative_reader import read_authoritative_snapshot

    snapshot = read_authoritative_snapshot(
        root / ".mission-state" / "sessions" / f"{session_id}.json",
        expected_session_id=session_id,
    )
    return snapshot.document_copy()


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue655_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _CurrentMission:
    def __init__(self, document: dict):
        self._document = document

    def document_copy(self) -> dict:
        return dict(self._document)


class _RecordingReinitializer:
    def __init__(self, document: dict):
        self.current = _CurrentMission(document)
        self.started = False

    def initialize(self, arguments: object) -> None:
        raise AssertionError("explicit new mission must not use plain initialize")

    def current_mission(self) -> _CurrentMission:
        return self.current

    def start_new_mission(self, arguments: object, current: object) -> None:
        self.started = True


def test_plain_init_command_intent_is_byte_identical_without_new_flag():
    cli = _load_cli_module()
    without_flag = SimpleNamespace(mission="unchanged command intent")
    explicit_false = SimpleNamespace(
        mission="unchanged command intent", new_mission=False
    )

    without_bytes = cli._canonical_init_command(without_flag)[1]
    false_bytes = cli._canonical_init_command(explicit_false)[1]

    assert false_bytes == without_bytes
    assert b"new_mission" not in false_bytes


def test_halted_session_requires_explicit_new_mission_and_archives_old_generation(
    tmp_path, run_cli
):
    """halt -> plain init rejection -> explicit archive/re-init breaks the dead end."""
    session_id = "issue-655-halted"
    env = {"MISSION_SESSION_ID": session_id}

    first = run_cli(
        "init",
        "first terminal mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert first.returncode == 0, first.stderr
    first_state = _authoritative_state(tmp_path, session_id)

    halted = run_cli(
        "mark-halt",
        "--reason",
        "first mission deliberately closed",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr

    plain = run_cli(
        "init",
        "second mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert plain.returncode == 2
    assert "session-already-initialized" in plain.stderr

    restarted = run_cli(
        "init",
        "second mission",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )
    assert restarted.returncode == 0, restarted.stderr
    output = json.loads(restarted.stdout)
    archived_to = Path(output["archived_to"])
    assert archived_to.is_file()
    assert "generations" in archived_to.parts

    archived = json.loads(archived_to.read_text(encoding="utf-8"))
    current = _authoritative_state(tmp_path, session_id)
    assert archived["mission_id"] == first_state["mission_id"]
    assert archived["mission"] == "first terminal mission"
    assert archived["loop_active"] is False
    assert current["mission"] == "second mission"
    assert current["mission_id"] != archived["mission_id"]
    assert current["loop_active"] is True


def test_new_mission_with_same_text_gets_new_identity_and_active_state(
    tmp_path, run_cli
):
    """A repeated mission description must not replay the prior genesis operation."""
    session_id = "issue-655-same-text"
    env = {"MISSION_SESSION_ID": session_id}
    mission = "repeatable description with a distinct mission generation"
    initialized = run_cli(
        "init",
        mission,
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    first = _authoritative_state(tmp_path, session_id)
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before repeating the same description",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr

    restarted = run_cli(
        "init",
        mission,
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert restarted.returncode == 0, restarted.stderr
    current = _authoritative_state(tmp_path, session_id)
    assert current["mission"] == mission
    assert current["mission_id"] != first["mission_id"]
    assert current["phase"] == "planning"
    assert current["loop_active"] is True
    assert current["halt_reason"] == ""


def test_new_mission_rejects_active_session_with_close_or_continue_guidance(
    tmp_path, run_cli
):
    session_id = "issue-655-active"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission still in progress",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    before = _authoritative_state(tmp_path, session_id)

    rejected = run_cli(
        "init",
        "must not replace active mission",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "session is active" in rejected.stderr
    assert "reactivate" in rejected.stderr
    assert "mark-halt" in rejected.stderr
    assert _authoritative_state(tmp_path, session_id) == before
    assert not list(
        (tmp_path / ".mission-state" / "archive").glob(
            "session-*/generations/*/sessions/*.json"
        )
    )


def test_new_mission_rejects_undecodable_session_and_guides_repair(tmp_path, run_cli):
    session_id = "issue-655-corrupt"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission before repository damage",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    state_path.write_bytes(b"{ broken repository head ][")

    rejected = run_cli(
        "init",
        "must not overwrite damaged repository",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "repository-format-invalid" in rejected.stderr
    assert "repair" in rejected.stderr.lower()
    assert state_path.read_bytes() == b"{ broken repository head ]["


def test_new_mission_rejects_undecodable_authoritative_document(
    tmp_path, run_cli
):
    """A valid head must not hide an undecodable referenced state generation."""
    session_id = "issue-655-corrupt-document"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission before state document damage",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr

    repository_root = tmp_path / ".mission-state"
    head_path = repository_root / "sessions" / f"{session_id}.json"
    head_before = head_path.read_bytes()
    head = json.loads(head_before)
    generation = json.loads(
        (repository_root / head["state_generation"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    state_object = repository_root / generation["state"]["object"]
    state_object.write_bytes(b"{ broken authoritative document ][")

    rejected = run_cli(
        "init",
        "must not overwrite damaged state generation",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "undecodable" in rejected.stderr
    assert "repair" in rejected.stderr.lower()
    assert head_path.read_bytes() == head_before
    assert not (repository_root / "archive").exists()


def test_new_mission_resets_observation_fields_and_readds_aggregate_membership(
    tmp_path, run_cli, push_provenance_score
):
    session_id = "issue-655-observations"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission with prior observations",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    push_provenance_score(tmp_path, env_extra=env)
    halted = run_cli(
        "mark-halt",
        "--reason",
        "observation fixture closed",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr
    terminal = _authoritative_state(tmp_path, session_id)
    assert terminal["score_history"]
    aggregate_before = json.loads(
        (tmp_path / ".mission-state" / "aggregate.json").read_text(encoding="utf-8")
    )
    assert session_id not in aggregate_before["active_sessions"]

    restarted = run_cli(
        "init",
        "fresh mission without prior observations",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )
    assert restarted.returncode == 0, restarted.stderr
    result = json.loads(restarted.stdout)
    archived = json.loads(Path(result["archived_to"]).read_text(encoding="utf-8"))
    current = _authoritative_state(tmp_path, session_id)
    assert archived["score_history"] == terminal["score_history"]
    assert current["score_history"] == []
    assert current.get("specialist_invocations") == []
    assert current.get("command_outcomes", []) == []
    assert current.get("verification_history", []) == []
    aggregate_after = json.loads(
        (tmp_path / ".mission-state" / "aggregate.json").read_text(encoding="utf-8")
    )
    assert aggregate_after["active_sessions"].count(session_id) == 1


def test_explicit_terminal_outcome_is_a_restartable_terminal_marker(
    tmp_path, run_cli
):
    from mission_application.lifecycle import InitRequest, initialize

    session_id = "issue-655-explicit-outcome"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission represented by an explicit terminal outcome",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    terminal = _authoritative_state(tmp_path, session_id)
    terminal.update(
        {
            "phase": "done",
            "loop_active": False,
            "passes": False,
            "halt_reason": "",
            "terminal_outcome": "incomplete",
        }
    )
    repository = _RecordingReinitializer(terminal)

    initialize(
        repository,
        InitRequest(arguments=SimpleNamespace(), new_mission=True),
    )

    assert repository.started is True


def test_new_mission_rejects_active_legacy_session_without_overwrite(
    tmp_path, legacy_run_cli
):
    session_id = "issue-655-active-legacy"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = legacy_run_cli(
        "init",
        "active retained legacy mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr

    state_path = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    before = state_path.read_bytes()
    restarted = legacy_run_cli(
        "init",
        "replacement must not overwrite retained legacy state",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert restarted.returncode == 2
    assert "reactivate" in restarted.stderr
    assert "mark-halt" in restarted.stderr
    assert "V5" in restarted.stderr
    assert state_path.read_bytes() == before


def test_invalid_new_mission_arguments_do_not_publish_an_archive(
    tmp_path, run_cli
):
    session_id = "issue-655-invalid-restart"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "terminal mission before invalid replacement",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before invalid replacement",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    before = state_path.read_bytes()

    rejected = run_cli(
        "init",
        "invalid replacement",
        "--complexity",
        "Standard",
        "--budget-minutes",
        "-1",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "budget-minutes" in rejected.stderr
    assert state_path.read_bytes() == before
    assert not list(
        (tmp_path / ".mission-state" / "archive").glob(
            "session-*/generations/*/sessions/*.json"
        )
    )


def test_new_mission_archives_assumptions_and_starts_with_an_empty_file(
    tmp_path, run_cli
):
    session_id = "issue-655-assumptions"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission with assumptions to preserve",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    first = _authoritative_state(tmp_path, session_id)
    old_assumptions = tmp_path / first["assumptions_path"]
    old_assumptions.write_text(
        "# Assumption Registry\nA_1: preserve this old assumption\n",
        encoding="utf-8",
    )
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed after recording assumptions",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr

    restarted = run_cli(
        "init",
        "new mission with isolated assumptions",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert restarted.returncode == 0, restarted.stderr
    output = json.loads(restarted.stdout)
    generation_root = Path(output["archived_to"]).parents[1]
    manifest = json.loads(
        (generation_root / "manifest.json").read_text(encoding="utf-8")
    )
    archived_assumptions = generation_root / manifest["assumptions"]["path"]
    assert archived_assumptions.read_bytes() == old_assumptions.read_bytes()
    current = _authoritative_state(tmp_path, session_id)
    assert current["assumptions_path"] != first["assumptions_path"]
    assert (tmp_path / current["assumptions_path"]).read_text(encoding="utf-8") == (
        "# Assumption Registry\n"
    )


def test_new_mission_simple_complexity_initializes_instead_of_goal_routing(
    tmp_path, run_cli
):
    session_id = "issue-655-simple"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "terminal standard mission before simple replacement",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before simple replacement",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr

    restarted = run_cli(
        "init",
        "simple replacement",
        "--complexity",
        "Simple",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert restarted.returncode == 0, restarted.stderr
    output = json.loads(restarted.stdout)
    assert output["ok"] is True
    assert "archived_to" in output
    current = _authoritative_state(tmp_path, session_id)
    assert current["mission"] == "simple replacement"
    assert current["loop_active"] is True


def test_archive_directory_fsync_failure_is_protocol_error(tmp_path, monkeypatch):
    from mission_persistence import administrative

    authority = tmp_path / ".mission-state"
    authority.mkdir()

    def fail_fsync(descriptor):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(administrative.os, "fsync", fail_fsync)
    with pytest.raises(administrative.AdministrativeCommitError) as failure:
        administrative.publish_administrative_generation(
            authority / "archive" / "session-test",
            generation="a" * 64,
            files={"sessions/test.json": b"{}"},
        )
    assert failure.value.code == "generation-publish-failed"


def test_generation_publish_does_not_mask_post_rename_fsync_failure(
    tmp_path, monkeypatch
):
    """A durable-publish failure after rename is not a collision success."""
    from mission_persistence import administrative

    authority = tmp_path / ".mission-state"
    authority.mkdir()
    real_rename = administrative.os.rename
    real_fsync = administrative.os.fsync
    renamed = False

    def mark_rename(*args, **kwargs):
        nonlocal renamed
        result = real_rename(*args, **kwargs)
        renamed = True
        return result

    def fail_only_after_rename(descriptor):
        if renamed:
            raise OSError("simulated post-rename fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(administrative.os, "rename", mark_rename)
    monkeypatch.setattr(administrative.os, "fsync", fail_only_after_rename)
    with pytest.raises(administrative.AdministrativeCommitError) as failure:
        administrative.publish_administrative_generation(
            authority / "archive" / "session-test",
            generation="d" * 64,
            files={"sessions/test.json": b"{}"},
        )
    assert failure.value.code == "generation-publish-failed"


def test_new_mission_reuses_its_own_orphaned_assumptions_reservation(
    tmp_path, run_cli
):
    """A crash after exclusive reservation may retry only the same head's record."""
    from mission_application.lifecycle import reinitialized_assumptions_path
    from mission_persistence.reinitialization import V5MissionReinitializer

    session_id = "issue-655-orphaned-reservation"
    env = {"MISSION_SESSION_ID": session_id}
    assert run_cli(
        "init", "terminal before reservation crash", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env,
    ).returncode == 0
    assert run_cli(
        "mark-halt", "--reason", "closed", "--category", "other",
        cwd=tmp_path, env_extra=env,
    ).returncode == 0
    repository = V5MissionReinitializer(
        tmp_path,
        tmp_path / ".mission-state" / "sessions" / f"{session_id}.json",
        lambda _arguments, _root: None,
    )
    current = repository.current_mission()
    repository._reserve_new_assumptions(
        reinitialized_assumptions_path(session_id, current.head_digest),
        current.head_digest,
    )
    repository._release_reservation()

    retried = run_cli(
        "init", "replacement after simulated crash", "--complexity", "Standard",
        "--new-mission", cwd=tmp_path, env_extra=env,
    )
    assert retried.returncode == 0, retried.stderr
    assert _authoritative_state(tmp_path, session_id)["mission"] == (
        "replacement after simulated crash"
    )


def test_assumptions_reservation_rejects_parent_symlink_swap(tmp_path, monkeypatch):
    """Reservation must not create through a pathname swapped after traversal."""
    from mission_persistence import reinitialization
    from mission_persistence.fenced_commit import FencedCommitError

    root = tmp_path
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    repository = reinitialization.V5MissionReinitializer(
        root, sessions / "session.json", lambda _arguments, _root: None
    )
    relative = ".mission-state/sessions/session-restart-assumptions.md"
    real_open = reinitialization.os.open
    swapped = False

    def swap_parent_before_create(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "session-restart-assumptions.md" and not swapped:
            swapped = True
            relocated = sessions.with_name("sessions-relocated")
            sessions.rename(relocated)
            sessions.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(reinitialization.os, "open", swap_parent_before_create)
    with pytest.raises(FencedCommitError) as failure:
        repository._reserve_new_assumptions(relative, "a" * 64)
    assert failure.value.code == "new-mission-assumptions-invalid"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("replacement", ["symlink", "hardlink"])
def test_new_mission_rejects_unsafe_assumptions_identity(
    tmp_path, run_cli, replacement
):
    session_id = f"issue-655-{replacement}-assumptions"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "mission with assumptions identity to protect",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before unsafe assumptions replacement",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    before = state_path.read_bytes()
    assumptions = tmp_path / _authoritative_state(tmp_path, session_id)[
        "assumptions_path"
    ]
    victim = tmp_path / "outside-assumptions.md"
    victim.write_text("OUTSIDE\n", encoding="utf-8")
    assumptions.unlink()
    if replacement == "symlink":
        assumptions.symlink_to(victim)
    else:
        os.link(victim, assumptions)

    rejected = run_cli(
        "init",
        "must reject unsafe assumptions identity",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "archive-assumptions-unreadable" in rejected.stderr
    assert state_path.read_bytes() == before
    assert not list(
        (tmp_path / ".mission-state" / "archive").glob(
            "session-*/generations/*/sessions/*.json"
        )
    )


def test_new_mission_rejects_preseeded_fresh_assumptions_path(
    tmp_path, run_cli
):
    from mission_application.lifecycle import reinitialized_assumptions_path
    from mission_persistence.fenced_commit import LocalFencedRepository

    session_id = "issue-655-preseed-assumptions"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "terminal mission before assumptions preseed",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before assumptions preseed",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    before = state_path.read_bytes()
    head_digest = LocalFencedRepository(
        tmp_path / ".mission-state"
    ).read(session_id).result.head_digest
    fresh = tmp_path / reinitialized_assumptions_path(session_id, head_digest)
    fresh.write_text("INJECTED\n", encoding="utf-8")

    rejected = run_cli(
        "init",
        "replacement must not inherit a preseeded assumptions file",
        "--complexity",
        "Standard",
        "--new-mission",
        cwd=tmp_path,
        env_extra=env,
    )

    assert rejected.returncode == 2
    assert "new-mission-assumptions-exists" in rejected.stderr
    assert state_path.read_bytes() == before
    assert fresh.read_text(encoding="utf-8") == "INJECTED\n"
    assert not list(
        (tmp_path / ".mission-state" / "archive").glob(
            "session-*/generations/*/sessions/*.json"
        )
    )


def test_generation_publish_fsyncs_every_nested_directory(tmp_path, monkeypatch):
    from mission_persistence import administrative

    authority = tmp_path / ".mission-state"
    authority.mkdir()
    real_fsync = administrative.os.fsync
    synced_directories = set()

    def record_fsync(descriptor):
        metadata = administrative.os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            synced_directories.add((metadata.st_dev, metadata.st_ino))
        return real_fsync(descriptor)

    monkeypatch.setattr(administrative.os, "fsync", record_fsync)
    administrative.publish_administrative_generation(
        authority / "archive" / "session-test",
        generation="b" * 64,
        files={
            "sessions/test.json": b"{}",
            "assumptions/test.md": b"# Assumption Registry\n",
            "manifest.json": b"{}",
        },
    )

    assert len(synced_directories) >= 7


def test_generation_publish_rejects_directory_identity_swap(
    tmp_path, monkeypatch
):
    from mission_persistence import administrative

    authority = tmp_path / ".mission-state"
    authority.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open_directory = administrative._open_directory_at
    swapped = False

    def swap_after_open(parent_fd, name, *, create=False):
        nonlocal swapped
        result = real_open_directory(parent_fd, name, create=create)
        if name == "generations" and not swapped:
            swapped = True
            generations = authority / "archive" / "session-test" / "generations"
            relocated = generations.with_name("generations-relocated")
            generations.rename(relocated)
            generations.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(administrative, "_open_directory_at", swap_after_open)
    with pytest.raises(administrative.AdministrativeCommitError) as failure:
        administrative.publish_administrative_generation(
            authority / "archive" / "session-test",
            generation="c" * 64,
            files={"sessions/test.json": b"{}"},
        )

    assert failure.value.code == "generation-changed"
    assert list(outside.iterdir()) == []


def test_assumptions_reader_rejects_parent_directory_identity_swap(
    tmp_path, monkeypatch
):
    from mission_kernel.errors import StrictReadError
    from mission_persistence import strict_reader

    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    assumptions = sessions / "assumptions.md"
    assumptions.write_text("ORIGINAL\n", encoding="utf-8")
    outside = tmp_path / "outside-sessions"
    outside.mkdir()
    (outside / assumptions.name).write_text("OUTSIDE\n", encoding="utf-8")
    real_open = strict_reader.os.open
    swapped = False

    def swap_parent_before_file_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == assumptions.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            relocated = sessions.with_name("sessions-relocated")
            sessions.rename(relocated)
            sessions.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(strict_reader.os, "open", swap_parent_before_file_open)
    with pytest.raises(StrictReadError) as failure:
        strict_reader.read_stable_bytes_beneath(
            tmp_path,
            ".mission-state/sessions/assumptions.md",
        )
    assert failure.value.code == "identity-changed"


@pytest.mark.parametrize("failure_point", ["marker", "carrier"])
def test_post_head_commit_failure_keeps_new_assumptions_reference(
    tmp_path, run_cli, monkeypatch, capsys, failure_point
):
    session_id = "issue-655-post-head-failure"
    env = {"MISSION_SESSION_ID": session_id}
    initialized = run_cli(
        "init",
        "terminal mission before post-head failure",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    halted = run_cli(
        "mark-halt",
        "--reason",
        "closed before post-head failure",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra=env,
    )
    assert halted.returncode == 0, halted.stderr

    module = _load_cli_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", session_id)
    monkeypatch.setenv(
        "MISSION_LEASE_ID",
        _authoritative_state(tmp_path, session_id)["lease_id"],
    )

    def fail_after_head_commit(*_args, **_kwargs):
        raise RuntimeError("simulated post-head failure")

    if failure_point == "marker":
        monkeypatch.setattr(
            module, "record_reinitialization_commit", fail_after_head_commit
        )
    else:
        monkeypatch.setattr(module, "_emit_lease_carrier", fail_after_head_commit)
    args = module._build_parser().parse_args(
        [
            "init",
            "new mission whose head is already committed",
            "--complexity",
            "Standard",
            "--new-mission",
        ]
    )
    with pytest.raises(RuntimeError, match="post-head failure"):
        module.cmd_init(args)

    current = _authoritative_state(tmp_path, session_id)
    assumptions = tmp_path / current["assumptions_path"]
    assert current["mission"] == "new mission whose head is already committed"
    assert current["loop_active"] is True
    assert assumptions.read_text(encoding="utf-8") == "# Assumption Registry\n"
    aggregate = json.loads(
        (tmp_path / ".mission-state" / "aggregate.json").read_text(
            encoding="utf-8"
        )
    )
    assert aggregate["active_sessions"].count(session_id) == 1
    failure_output = json.loads(capsys.readouterr().out)
    assert failure_output["ok"] is False
    assert Path(failure_output["archived_to"]).is_file()
