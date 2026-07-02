"""P3: mission-stop-guard.sh の並列 owner 照合 (session env ベース、AGENT_PID 非依存)."""
import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[3] / "scripts" / "mission-stop-guard.sh"


def _run_hook(cwd, env_extra):
    env = {"PATH": os.environ["PATH"], "MISSION_HOOK_CWD": str(cwd)}
    env.update(env_extra)
    return subprocess.run(["bash", str(HOOK)], input='{"stop_hook_active":false}',
                          capture_output=True, text=True, env=env)


def _run_hook_with_input(cwd, env_extra, *, timeout=None):
    env = {"PATH": os.environ["PATH"]}
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"stop_hook_active": False, "cwd": str(cwd)}),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _write_session(cwd, sid, **kw):
    sd = cwd / ".mission-state" / "sessions"; sd.mkdir(parents=True, exist_ok=True)
    base = {"loop_active": True, "passes": False, "halt_reason": "", "pid": os.getpid(),
            "project_root": str(cwd), "mission": "g", "iteration": 0, "threshold": 4.0, "score_history": []}
    base.update(kw)
    (sd / f"{sid}.json").write_text(json.dumps(base))


def test_hook_blocks_own_session(tmp_path):
    """自分の session_id (cc-mine) の未達 state は block される."""
    _write_session(tmp_path, "cc-mine")
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "mine"})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_ignores_other_session(tmp_path):
    """別 session_id (cc-other) の state は自分のではないので block しない (並列分離)."""
    _write_session(tmp_path, "cc-other")
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "mine"})
    assert "block" not in r.stdout


def test_hook_blocks_completed_session_no(tmp_path):
    """自分の session でも passes=true なら block しない (完了)."""
    _write_session(tmp_path, "cc-mine", passes=True)
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "mine"})
    assert "block" not in r.stdout


def test_hook_codex_session(tmp_path):
    """Codex の CODEX_THREAD_ID でも cx- prefix で owner 照合する."""
    _write_session(tmp_path, "cx-thread1")
    r = _run_hook(tmp_path, {"CODEX_THREAD_ID": "thread1"})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_sanitizes_session_id(tmp_path):
    """MISSION_SESSION_ID に / が含まれてもサニタイズ後の sf 名 (a_b) と一致して block する."""
    _write_session(tmp_path, "a_b")
    r = _run_hook(tmp_path, {"MISSION_SESSION_ID": "a/b"})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_pid_fallback(tmp_path):
    """session env が無い環境では pid 照合 fallback で block する."""
    _write_session(tmp_path, "legacy-x", pid=os.getpid())
    r = _run_hook(tmp_path, {"MISSION_HOOK_AGENT_PID": str(os.getpid())})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_blocks_own_session_even_if_pid_dead(tmp_path):
    """sid 一致なら state の pid が dead (resume/compaction で PID 変化) でも block する (M-1)."""
    _write_session(tmp_path, "cc-mine", pid=999999)  # dead pid
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "mine"})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_sanitizes_leading_dot(tmp_path):
    """#14: MISSION_SESSION_ID 先頭ドットは py _sanitize_sid 同様 strip され sf 名と一致して block."""
    _write_session(tmp_path, "weird")           # py は ".weird" → "weird" に sanitize して書く
    r = _run_hook(tmp_path, {"MISSION_SESSION_ID": ".weird"})
    assert '"decision"' in r.stdout and "block" in r.stdout


def test_hook_orphan_halt_when_envless_and_pid_dead(tmp_path):
    """#13: env-less fallback + dead pid の orphan は halt_reason='orphan:' が書かれ block しない.

    (この orphan-halt write は env-less かつ死PID時のみ発火する希少パス。対象は dead orphan で
     競合 writer がないため unlocked でも idempotent — #13 で option(b) 維持と判断した挙動の特性テスト)
    """
    _write_session(tmp_path, "s-orphan", pid=999999, project_root=str(tmp_path))
    r = _run_hook(tmp_path, {})  # session env なし = env-less pid fallback
    assert "block" not in r.stdout
    st = json.loads((tmp_path / ".mission-state" / "sessions" / "s-orphan.json").read_text())
    assert st["halt_reason"].startswith("orphan:") and st["loop_active"] is False


def test_hook_warns_on_stale_state(tmp_path):
    """F-5: updated_at が1h超〜3h未満の state は block 理由に WARN を前置する.
    (3h 超は Issue #1 により auto-halt に変更されたため、このテストは2時間前のタイムスタンプを使う)
    """
    import datetime
    two_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(tmp_path, "cc-stale", updated_at=two_hours_ago)
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "stale"})
    assert "block" in r.stdout and "WARN" in r.stdout


def test_hook_autohalts_on_very_stale_state(tmp_path):
    """Issue #1: updated_at が3h超(2020) の state は block せず、session file を auto-halt する."""
    _write_session(tmp_path, "cc-stale2", updated_at="2020-01-01T00:00:00Z", project_root=str(tmp_path))
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "stale2"})
    # hook は block を返さない (decision:block が stdout にない)
    assert "block" not in r.stdout, f"auto-halt 対象なのに block が返った: {r.stdout}"
    # session file が halt されている
    sf = tmp_path / ".mission-state" / "sessions" / "cc-stale2.json"
    st = json.loads(sf.read_text())
    assert st["loop_active"] is False, "loop_active が false になっていない"
    assert "stale" in st["halt_reason"], f"halt_reason に 'stale' が含まれない: {st['halt_reason']}"


def test_hook_does_not_autohalt_awaiting_user_state(tmp_path):
    """#97: awaiting_user=true の人間待ち state は stale auto-halt しない。"""
    _write_session(
        tmp_path,
        "cc-waiting",
        updated_at="2020-01-01T00:00:00Z",
        project_root=str(tmp_path),
        awaiting_user=True,
    )

    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "waiting"})

    assert "block" in r.stdout, r.stdout
    st = json.loads((tmp_path / ".mission-state" / "sessions" / "cc-waiting.json").read_text())
    assert st["loop_active"] is True
    assert st["halt_reason"] == ""


def test_hook_lsof_timeout_falls_back_to_input_cwd(tmp_path):
    """#94: slow lsof で hook 全体が固まらず、input .cwd へ降下して block する。"""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "ps").write_text("#!/usr/bin/env bash\nprintf 'codex\\n'\n")
    (fake_bin / "readlink").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "lsof").write_text("#!/usr/bin/env bash\nsleep 5\n")
    (fake_bin / "ps").chmod(0o755)
    (fake_bin / "readlink").chmod(0o755)
    (fake_bin / "lsof").chmod(0o755)
    _write_session(tmp_path, "cc-slow")

    r = _run_hook_with_input(
        tmp_path,
        {"PATH": f"{fake_bin}:{os.environ['PATH']}", "CLAUDE_CODE_SESSION_ID": "slow"},
        timeout=4,
    )

    assert "block" in r.stdout, r.stdout


def test_hook_no_warn_on_fresh_state(tmp_path):
    """F-5 負テスト: 直近更新(2分前)の state では STALE WARN を出さない(JST誤発火回帰)."""
    import datetime
    fresh = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(tmp_path, "cc-fresh", updated_at=fresh)
    r = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "fresh"})
    assert "block" in r.stdout and "WARN" not in r.stdout, "fresh state で誤って STALE WARN が出た(tzバグ)"


def test_hook_does_not_contaminate_other_session_after_own_autohalt(tmp_path):
    """(a) 他セッション非汚染: 自session stale auto-halt 後も別sid fresh stateは block を返す."""
    import datetime
    very_stale = "2020-01-01T00:00:00Z"
    fresh = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 自セッション (cc-mine) が stale (3h超) → auto-halt 対象
    _write_session(tmp_path, "cc-mine", updated_at=very_stale, project_root=str(tmp_path))
    # 別セッション (cc-other) が fresh かつ未達
    _write_session(tmp_path, "cc-other", updated_at=fresh, project_root=str(tmp_path))

    # 自セッション (cc-mine) の hook 呼び出し → stale なので block しない
    r_self = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "mine"})
    assert "block" not in r_self.stdout, f"自session stale は block すべきでない: {r_self.stdout}"

    # 自セッション auto-halt 後、cc-other を持つ別エージェントが hook を呼ぶ → fresh なので block
    r_other = _run_hook(tmp_path, {"CLAUDE_CODE_SESSION_ID": "other"})
    assert '"decision"' in r_other.stdout and "block" in r_other.stdout, \
        f"fresh な別session は block されるべき: {r_other.stdout}"

    # cc-other の loop_active が true のまま (auto-halt で汚染されていない)
    sf_other = tmp_path / ".mission-state" / "sessions" / "cc-other.json"
    st_other = json.loads(sf_other.read_text())
    assert st_other["loop_active"] is True, "別session の loop_active が変更されている (汚染)"


def test_hook_custom_stale_halt_seconds(tmp_path):
    """(b) MISSION_STALE_HALT_SECONDS=400: 下限(300)以上の値で500秒超の state が auto-halt される."""
    import datetime
    # 500秒前のタイムスタンプ (400秒の閾値を超える; 下限300以上の値が有効になる)
    old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=500)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(tmp_path, "cc-custom", updated_at=old_ts, project_root=str(tmp_path))

    env = {"CLAUDE_CODE_SESSION_ID": "custom", "MISSION_STALE_HALT_SECONDS": "400"}
    r = _run_hook(tmp_path, env)
    # 500秒超 > 400秒閾値 なので auto-halt → block しない
    assert "block" not in r.stdout, f"400s threshold で auto-halt すべき: {r.stdout}"
    sf = tmp_path / ".mission-state" / "sessions" / "cc-custom.json"
    st = json.loads(sf.read_text())
    assert st["loop_active"] is False, "loop_active が false になっていない"
    assert "stale" in st["halt_reason"], f"halt_reason に 'stale' が含まれない: {st['halt_reason']}"


# ===== P1-2: planning 滞留(push-score 未実行)の bd12 型捏造検出 =====


def test_hook_warns_push_score_not_executed_when_planning_stale(tmp_path):
    """P1-2: loop_active=true かつ score_history 空 かつ planning 滞留が閾値超で警告注入.

    bd12 型 (phase=planning/iteration=0/score_history 空 のまま「実行した体」で捏造) を
    早期検出するため、feedback に push-score 未実行の疑いを警告する。
    MISSION_PLANNING_WARN_ITERATIONS 環境変数で閾値調整可能 (デフォルト=3, テストは1を使用)。
    block はしない (警告注入のみ)。
    """
    import datetime
    # 更新時刻は直近(1分前)なので stale auto-halt には引っかからない
    fresh = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # phase=planning, iteration=1 (閾値1を超える), score_history 空
    _write_session(tmp_path, "cc-bdtest",
                   updated_at=fresh,
                   phase="planning",
                   iteration=1,
                   score_history=[])
    env = {
        "CLAUDE_CODE_SESSION_ID": "bdtest",
        "MISSION_PLANNING_WARN_ITERATIONS": "1",  # テスト用閾値: iteration >= 1 で発火
    }
    r = _run_hook(tmp_path, env)
    # block は維持される(未達 state)
    assert '"decision"' in r.stdout and "block" in r.stdout, f"block が返るべき: {r.stdout}"
    # feedback に push-score 未実行の警告が含まれる
    assert "push-score" in r.stdout, f"push-score 警告が feedback にない: {r.stdout}"


def test_hook_no_warn_on_fresh_planning(tmp_path):
    """P1-2 負テスト: iteration が閾値以下の正常な planning 初期では警告を出さない (偽陽性防止)."""
    import datetime
    fresh = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # iteration=0 は planning 開始直後 → 閾値(1)以下なので警告なし
    _write_session(tmp_path, "cc-freshplan",
                   updated_at=fresh,
                   phase="planning",
                   iteration=0,
                   score_history=[])
    env = {
        "CLAUDE_CODE_SESSION_ID": "freshplan",
        "MISSION_PLANNING_WARN_ITERATIONS": "1",
    }
    r = _run_hook(tmp_path, env)
    assert "block" in r.stdout, f"未達なので block すべき: {r.stdout}"
    assert "push-score" not in r.stdout, f"iteration=0 では push-score 警告不要: {r.stdout}"


def test_hook_planning_warn_iter_zero_clamped_to_default(tmp_path):
    """stop-guard 下限ガード: MISSION_PLANNING_WARN_ITERATIONS=0 は下限 3 にクランプされ
    iteration=0 では push-score 警告を出さない (下限ガードが無いと iter0 でも誤発火する)."""
    import datetime
    fresh = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(tmp_path, "cc-zerothresh",
                   updated_at=fresh,
                   phase="planning",
                   iteration=0,
                   score_history=[])
    env = {
        "CLAUDE_CODE_SESSION_ID": "zerothresh",
        "MISSION_PLANNING_WARN_ITERATIONS": "0",  # 下限ガードで 3 にクランプされるべき
    }
    r = _run_hook(tmp_path, env)
    assert "block" in r.stdout, f"未達なので block すべき: {r.stdout}"
    assert "push-score" not in r.stdout, f"iter=0 で PLANNING_WARN_ITERATIONS=0 の場合、下限ガードで警告が出ないはず: {r.stdout}"
