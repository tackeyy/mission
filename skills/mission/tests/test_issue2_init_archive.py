"""Issue #2: 別 mission_id で init すると旧 score_history が archive に退避される。"""
import json
from pathlib import Path


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


def test_init_archives_old_state_on_mission_change(tmp_path, run_cli):
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

    # Push a score so score_history is non-empty
    _push_score(run_cli, tmp_path, sid=sid)

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
