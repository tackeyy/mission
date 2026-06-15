"""Issue #5: init 後に assumptions_path のファイルが存在することを確認."""
import json
from pathlib import Path


def test_init_creates_assumptions_file(tmp_path, run_cli):
    """init 後に assumptions_path のファイルが存在する。"""
    r = run_cli(
        "init", "test mission for assumptions",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "test-sess"},
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True

    # state から assumptions_path を読む
    sf = Path(out["session_file"])
    data = json.loads(sf.read_text())
    ap = data["assumptions_path"]
    assert ap, "assumptions_path が空"

    assumptions_file = tmp_path / ap
    assert assumptions_file.exists(), f"assumptions_path のファイルが存在しない: {ap}"
    content = assumptions_file.read_text()
    assert "Assumption Registry" in content, "テンプレ内容が正しくない"


def test_init_assumptions_file_not_overwritten_on_resume(tmp_path, run_cli):
    """同 session_id で再 init (resume) しても既存 assumptions ファイルを上書きしない。"""
    # 1回目 init
    r1 = run_cli(
        "init", "first mission",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "resume-sess"},
    )
    assert r1.returncode == 0, r1.stderr
    out1 = json.loads(r1.stdout)
    ap = tmp_path / json.loads(Path(out1["session_file"]).read_text())["assumptions_path"]
    ap.write_text("# Assumption Registry\nA_1: custom entry\n")

    # 2回目 init (resume)
    r2 = run_cli(
        "init", "resumed mission",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "resume-sess"},
    )
    assert r2.returncode == 0, r2.stderr

    # 既存の内容が保持されている
    assert "custom entry" in ap.read_text()
