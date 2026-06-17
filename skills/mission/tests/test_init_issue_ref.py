"""S3: init --issue-ref 引数と同一 issue_ref の重複 WARN テスト."""
import json
import os
import subprocess
import sys
from pathlib import Path

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _run(args, cwd, env_extra=None):
    """テスト用 CLI 実行ヘルパー (conftest.run_cli と同等)."""
    _SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith("MISSION_") and k not in _SESSION_ENV_VARS}
    _sid_keys = ("MISSION_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
    if not (env_extra and any(k in env_extra for k in _sid_keys)):
        base_env["MISSION_SESSION_ID"] = "test"
    if env_extra:
        base_env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(MISSION_STATE_PY), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=base_env,
    )


def _read(path):
    return json.loads((path / ".mission-state" / "sessions" / "test.json").read_text())


# ===== --issue-ref 基本保存 =====


def test_init_issue_ref_saved(tmp_path):
    """--issue-ref で指定した値が state.issue_ref に保存される."""
    r = _run(["init", "S3 test mission", "--issue-ref", "github:owner/repo#42"], cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = _read(tmp_path)
    assert s.get("issue_ref") == "github:owner/repo#42"


def test_init_without_issue_ref_is_none(tmp_path):
    """--issue-ref 未指定 → issue_ref=None (後方互換)."""
    r = _run(["init", "S3 no-ref mission"], cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = _read(tmp_path)
    # None または キー自体が存在しない (どちらも後方互換)
    assert s.get("issue_ref") is None


# ===== 重複 issue_ref WARN =====


def test_init_dup_issue_ref_warns(tmp_path):
    """同プロジェクト内に同一 issue_ref + loop_active=True の state がある場合 stderr WARN."""
    # セッション A (別 sid) で先に init → active state を作る
    r_a = _run(["init", "S3 first mission", "--issue-ref", "gh:repo#99"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    assert r_a.returncode == 0, f"session-a stderr: {r_a.stderr}"

    # セッション B (別 sid) で同一 issue_ref を指定
    r_b = _run(["init", "S3 second mission", "--issue-ref", "gh:repo#99"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r_b.returncode == 0, f"session-b stderr: {r_b.stderr}"
    # WARN が出ること
    assert "warn" in r_b.stderr.lower() or "warning" in r_b.stderr.lower(), (
        f"expected WARN for dup issue_ref, got: {r_b.stderr!r}"
    )
    assert "issue_ref" in r_b.stderr.lower() or "gh:repo#99" in r_b.stderr, (
        f"WARN should mention issue_ref, got: {r_b.stderr!r}"
    )


def test_init_dup_issue_ref_inactive_no_warn(tmp_path):
    """同 issue_ref でも loop_active=False の state は WARN しない."""
    # セッション A: init して loop_active=false にする
    r_a = _run(["init", "S3 done mission", "--issue-ref", "gh:repo#77"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    assert r_a.returncode == 0
    # loop_active を False に書き換え
    sf = tmp_path / ".mission-state" / "sessions" / "session-a.json"
    data = json.loads(sf.read_text())
    data["loop_active"] = False
    sf.write_text(json.dumps(data))

    # セッション B: 同一 issue_ref → WARN しない
    r_b = _run(["init", "S3 second mission", "--issue-ref", "gh:repo#77"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r_b.returncode == 0
    # issue_ref の重複 WARN が出ないこと
    warn_lines = [ln for ln in r_b.stderr.splitlines()
                  if ("warn" in ln.lower() or "warning" in ln.lower())
                  and "issue_ref" in ln.lower()]
    assert warn_lines == [], f"unexpected issue_ref WARN: {r_b.stderr}"


def test_init_no_issue_ref_no_dup_warn(tmp_path):
    """--issue-ref 未指定同士では重複チェックしない (None は比較対象外)."""
    _run(["init", "S3 no-ref A"], cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    r_b = _run(["init", "S3 no-ref B"], cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r_b.returncode == 0
    warn_lines = [ln for ln in r_b.stderr.splitlines()
                  if ("warn" in ln.lower() or "warning" in ln.lower())
                  and "issue_ref" in ln.lower()]
    assert warn_lines == [], f"unexpected issue_ref WARN: {r_b.stderr}"


def test_init_dup_issue_ref_does_not_reject(tmp_path):
    """WARN が出ても init は成功する (reject しない)."""
    _run(["init", "S3 first", "--issue-ref", "gh:repo#55"],
         cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-a"})
    r_b = _run(["init", "S3 second", "--issue-ref", "gh:repo#55"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-b"})
    assert r_b.returncode == 0, f"init should succeed even with dup issue_ref: {r_b.stderr}"
    # state が作られていること
    sf_b = tmp_path / ".mission-state" / "sessions" / "session-b.json"
    assert sf_b.exists(), "session-b state file should be created"
    s = json.loads(sf_b.read_text())
    assert s["issue_ref"] == "gh:repo#55"


def test_s3_same_session_id_no_warn_on_resume(tmp_path):
    """S3: 同一 session_id (= resume) では自分自身の旧 state を誤検出して WARN しない."""
    # セッション A: init して state を残す
    r_a = _run(["init", "S3 resume test", "--issue-ref", "gh:repo#99"],
               cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-resume"})
    assert r_a.returncode == 0, r_a.stderr

    # 同一 session_id で再度 init (resume 相当)
    r_resume = _run(["init", "S3 resume test again", "--issue-ref", "gh:repo#99"],
                    cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "session-resume"})
    assert r_resume.returncode == 0, r_resume.stderr

    # 自分自身の旧 state を誤検出した WARN が出ないこと
    warn_lines = [ln for ln in r_resume.stderr.splitlines()
                  if ("warn" in ln.lower() or "warning" in ln.lower())
                  and "issue_ref" in ln.lower()]
    assert warn_lines == [], (
        f"同一 sid の resume では S3 WARN を出してはいけない: {r_resume.stderr}"
    )
