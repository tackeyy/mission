"""S3-files: init --files による active session のファイル集合重複 WARN テスト."""
import json


def _state(tmp_path, sid="test"):
    return json.loads((tmp_path / ".mission-state" / "sessions" / f"{sid}.json").read_text())


def _file_warn_lines(stderr):
    return [ln for ln in stderr.splitlines() if "[S3-files]" in ln]


def test_init_files_saved(run_cli, tmp_path):
    """--files のカンマ区切りが state.planned_files に保存される."""
    r = run_cli("init", "S3 files", "--files", "a.py, b/c.py", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert _state(tmp_path)["planned_files"] == ["a.py", "b/c.py"]


def test_init_without_files_has_no_file_warn(run_cli, tmp_path):
    """--files 未指定では S3-files WARN を出さず、planned_files は空 list."""
    run_cli("init", "first", "--files", "a.py", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    r = run_cli("init", "second", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r.returncode == 0, r.stderr
    assert _file_warn_lines(r.stderr) == []
    assert _state(tmp_path, "session-b")["planned_files"] == []


def test_init_file_overlap_warns(run_cli, tmp_path):
    """別 active session と planned_files が重複した場合 stderr に [S3-files] WARN."""
    run_cli(
        "init", "first", "--files", "skills/a.py,skills/b.py",
        cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"},
    )
    r = run_cli(
        "init", "second", "--files", "docs/readme.md,skills/b.py",
        cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"},
    )
    assert r.returncode == 0, r.stderr
    assert "[S3-files]" in r.stderr
    assert "skills/b.py" in r.stderr


def test_init_file_non_overlap_no_warn(run_cli, tmp_path):
    """planned_files が重複しない active session では WARN しない."""
    run_cli("init", "first", "--files", "a.py", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    r = run_cli("init", "second", "--files", "b.py", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r.returncode == 0, r.stderr
    assert _file_warn_lines(r.stderr) == []


def test_init_file_same_session_resume_no_warn(run_cli, tmp_path):
    """同一 session_id の再 init は自分自身を overlap WARN しない."""
    env = {"MISSION_SESSION_ID": "session-resume"}
    run_cli("init", "first", "--files", "a.py", cwd=tmp_path, env_extra=env)
    r = run_cli("init", "second", "--files", "a.py", cwd=tmp_path, env_extra=env)
    assert r.returncode == 0, r.stderr
    assert _file_warn_lines(r.stderr) == []
