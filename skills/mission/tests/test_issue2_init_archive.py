"""Issue #2 / #302: 別 mission_id の state と assumptions を安全に退避する。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue302", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _push_score(run_cli, tmp_path, composite=4.5, min_item=4.0, sid="arch-sess"):
    items = {"mission_achievement": composite}
    run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", str(composite),
        "--min-item", str(min_item),
        "--items", json.dumps(items),
        cwd=tmp_path,
        check=True,
        env_extra={"MISSION_SESSION_ID": sid},
    )


def test_init_archives_old_state_on_mission_change(tmp_path, run_cli, push_provenance_score):
    """同 sid で別ミッション (別 mission_id) を init すると archive に退避される。"""
    sid = "arch-sess"

    # 1st init with mission A
    r1 = run_cli("init", "first mission alpha", cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})
    assert r1.returncode == 0, r1.stderr
    out1 = json.loads(r1.stdout)
    sf = Path(out1["session_file"])
    data1 = json.loads(sf.read_text())
    mid1 = data1["mission_id"]
    assumptions_file = tmp_path / data1["assumptions_path"]
    assumptions_file.write_text("# Assumption Registry\nA_1: first mission scope\n")

    # Push a score so score_history is non-empty
    push_provenance_score(tmp_path, env_extra={"MISSION_SESSION_ID": sid})

    # Re-read to confirm score_history exists
    score_data = json.loads(sf.read_text())
    assert score_data["score_history"], "push-score が効いていない"

    # 2nd init with a DIFFERENT mission (must produce different mission_id)
    r2 = run_cli("init", "second mission beta completely different text xyz789", cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})
    assert r2.returncode == 0, r2.stderr
    data2 = json.loads(sf.read_text())
    mid2 = data2["mission_id"]

    # Confirm mission_id changed (different missions)
    assert mid1 != mid2, "mission_id が変わっていない — テスト前提が崩れている"

    # Archive file should exist
    archive_dir = tmp_path / ".mission-state" / "archive"
    old_mid8 = mid1[:8]
    expected_archive = archive_dir / f"state-{sid}-{old_mid8}.json"
    assert expected_archive.exists(), f"archive ファイルが存在しない: {expected_archive}"

    # Archived data should retain old score_history
    archived = json.loads(expected_archive.read_text())
    assert archived["score_history"], "archive に score_history が保持されていない"
    assert archived["mission_id"] == mid1, "archive の mission_id が旧ミッションと一致しない"

    expected_assumptions_archive = archive_dir / f"state-{sid}-{old_mid8}-assumptions.md"
    assert expected_assumptions_archive.exists(), (
        f"assumptions archive が存在しない: {expected_assumptions_archive}"
    )
    assert "first mission scope" in expected_assumptions_archive.read_text()
    new_assumptions_file = tmp_path / data2["assumptions_path"]
    assert new_assumptions_file != assumptions_file
    assert new_assumptions_file.read_text() == "# Assumption Registry\n"
    assert "first mission scope" in assumptions_file.read_text()


def test_init_mission_change_rejects_archive_symlink(tmp_path, run_cli):
    sid = "archive-symlink"
    first = run_cli(
        "init", "first mission", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert first.returncode == 0, first.stderr
    state = json.loads(Path(json.loads(first.stdout)["session_file"]).read_text())
    assumptions = tmp_path / state["assumptions_path"]
    assumptions.write_text("# Assumption Registry\nprivate scope\n")

    archive = tmp_path / ".mission-state" / "archive"
    outside = tmp_path / "outside"
    outside.mkdir()
    archive.symlink_to(outside, target_is_directory=True)

    second = run_cli(
        "init", "second mission", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )

    assert second.returncode == 2
    assert list(outside.iterdir()) == []
    current = json.loads(Path(json.loads(first.stdout)["session_file"]).read_text())
    assert current["mission"] == "first mission"


def test_atomic_assumptions_write_does_not_follow_predictable_tmp_symlink(
    tmp_path, run_cli
):
    sid = "tmp-symlink"
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    assumptions = sessions / f"{sid}-assumptions.md"
    victim = tmp_path / "victim.txt"
    victim.write_text("do not overwrite\n")
    assumptions.with_suffix(".md.tmp").symlink_to(victim)

    result = run_cli(
        "init", "safe temp mission", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )

    assert result.returncode == 0, result.stderr
    assert victim.read_text() == "do not overwrite\n"
    assert assumptions.read_text() == "# Assumption Registry\n"


def test_init_mission_change_assumptions_failure_leaves_old_state(
    tmp_path, run_cli, monkeypatch, capsys
):
    sid = "assumptions-failure"
    first = run_cli(
        "init", "mission A", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert first.returncode == 0, first.stderr
    state_file = Path(json.loads(first.stdout)["session_file"])
    state_a = json.loads(state_file.read_text())
    assumptions_a = tmp_path / state_a["assumptions_path"]
    assumptions_a.write_text("# Assumption Registry\nmission A scope\n")

    module = _load_state_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", sid)
    monkeypatch.setattr(
        module,
        "atomic_write_text",
        lambda _path, _content: (_ for _ in ()).throw(PermissionError("denied")),
    )
    args = SimpleNamespace(
        mission="mission B",
        complexity="Standard",
        threshold=4.0,
        max_iter=None,
        issue_ref=None,
        files=None,
        review_tier=None,
    )

    with pytest.raises(SystemExit) as exc:
        module.cmd_init(args)

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert '"target": "assumptions"' in output
    current = json.loads(state_file.read_text())
    assert current["mission"] == "mission A"
    assert current["loop_active"] is True
    assert current["assumptions_path"] == state_a["assumptions_path"]
    assert "mission A scope" in assumptions_a.read_text()


def test_init_mission_change_archive_write_failure_leaves_old_state(
    tmp_path, run_cli, monkeypatch, capsys
):
    sid = "archive-failure"
    first = run_cli(
        "init", "mission A", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert first.returncode == 0, first.stderr
    state_file = Path(json.loads(first.stdout)["session_file"])
    state_a = json.loads(state_file.read_text())
    assumptions_a = tmp_path / state_a["assumptions_path"]
    assumptions_a.write_text("# Assumption Registry\nmission A scope\n")

    module = _load_state_module()
    real_atomic_write_bytes = module.atomic_write_bytes

    def deny_archive(path, content):
        if path.parent.name == "archive":
            raise PermissionError("denied")
        return real_atomic_write_bytes(path, content)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", sid)
    monkeypatch.setattr(module, "atomic_write_bytes", deny_archive)
    args = SimpleNamespace(
        mission="mission B",
        complexity="Standard",
        threshold=4.0,
        max_iter=None,
        issue_ref=None,
        files=None,
        review_tier=None,
    )

    with pytest.raises(SystemExit) as exc:
        module.cmd_init(args)

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert '"target": "archive"' in output
    current = json.loads(state_file.read_text())
    assert current["mission"] == "mission A"
    assert current["loop_active"] is True
    assert current["assumptions_path"] == state_a["assumptions_path"]
    assert "mission A scope" in assumptions_a.read_text()


def test_backup_and_archive_files_do_not_follow_symlinks(tmp_path, run_cli):
    sid = "file-symlink"
    first = run_cli(
        "init", "mission A", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert first.returncode == 0, first.stderr
    state_file = Path(json.loads(first.stdout)["session_file"])
    state_a = json.loads(state_file.read_text())
    victim = tmp_path / "victim.txt"
    victim.write_text("do not overwrite\n")

    backup = state_file.with_suffix(".json.bak")
    backup.unlink(missing_ok=True)
    backup.symlink_to(victim)
    resumed = run_cli(
        "init", "mission A", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert resumed.returncode == 0, resumed.stderr
    assert victim.read_text() == "do not overwrite\n"
    assert backup.is_file() and not backup.is_symlink()

    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir()
    archive_state = archive / f"state-{sid}-{state_a['mission_id'][:8]}.json"
    archive_state.symlink_to(victim)
    changed = run_cli(
        "init", "mission B", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": sid},
    )
    assert changed.returncode == 0, changed.stderr
    assert victim.read_text() == "do not overwrite\n"
    assert archive_state.is_file() and not archive_state.is_symlink()


def test_init_no_archive_on_resume_same_mission(tmp_path, run_cli):
    """同 sid・同 mission_id (resume) では archive を作らない。"""
    sid = "resume-sess2"
    mission_text = "resumable mission gamma"

    r1 = run_cli("init", mission_text, cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})
    assert r1.returncode == 0

    # resume with same mission (same mission_id)
    r2 = run_cli("init", mission_text, cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})
    assert r2.returncode == 0

    archive_dir = tmp_path / ".mission-state" / "archive"
    archives = list(archive_dir.glob("state-*-*.json")) if archive_dir.exists() else []
    # session-specific archives should not be created
    assert not archives, f"resume なのに archive が作られた: {archives}"


def test_init_quarantines_corrupt_session_json_on_mission_change(tmp_path, run_cli):
    """破損 session JSON があっても init は成功し、破損ファイルを退避する。"""
    sid = "corrupt-sess"
    r1 = run_cli("init", "first mission before corruption", cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})
    assert r1.returncode == 0, r1.stderr
    sf = Path(json.loads(r1.stdout)["session_file"])
    sf.write_text("{ broken ][")

    r2 = run_cli("init", "second mission after corruption", cwd=tmp_path,
                 env_extra={"MISSION_SESSION_ID": sid})

    assert r2.returncode == 0, r2.stderr
    assert "WARNING" in r2.stderr
    quarantined = list(sf.parent.glob(f"{sid}.json.corrupt-*"))
    assert quarantined, "破損 session JSON が .corrupt-* に退避されていない"
    assert json.loads(sf.read_text())["mission"] == "second mission after corruption"
