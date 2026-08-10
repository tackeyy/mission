"""Issue #377: 並列 mission 公式サポートの回帰テスト.

テスト観点:
AC-2: 並列 2 mission の init → 双方 set → 片方 mark-passes →
      stop-guard が未達の残 1 件を検出し、passed 済みの継続を要求しない。
AC-3: stop-guard の block メッセージに未達セッションの session_id / issue_ref が含まれる。
AC-4: 並列命名規約 (<base>-m<issue>) で同一 issue_ref を重複 init すると警告が出る。
"""
import json
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate

HOOK = Path(__file__).resolve().parents[3] / "scripts" / "mission-stop-guard.sh"
MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

_ITEMS = (
    '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.5,'
    '"usability":4.5,"reviewer_consensus":4.5}'
)


# ---------------------------------------------------------------------------
# helper: stop-guard を MISSION_SESSION_ID 指定で実行する
# ---------------------------------------------------------------------------

def _run_hook(cwd, session_id):
    env = {
        "PATH": os.environ["PATH"],
        "MISSION_HOOK_CWD": str(cwd),
        "MISSION_SESSION_ID": session_id,
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        input='{"stop_hook_active":false}',
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# helper: mission-state.py をサブプロセスで呼ぶ (conftest.run_cli と同等だが
# 複数 session を操作するため session_id を都度切り替えられるよう独立実装)
# ---------------------------------------------------------------------------

_SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def _run_state(args, cwd, *, session_id, lease_id="test-lease", env_extra=None):
    """mission-state.py をサブプロセスで実行する.

    lease_id=None を指定すると MISSION_LEASE_ID を環境変数から除去し、
    init が新しい lease_id を自動生成するようになる (独立 lease のテスト用)。
    """
    base_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("MISSION_") and k not in _SESSION_ENV_VARS
    }
    base_env["MISSION_SESSION_ID"] = session_id
    if lease_id is not None:
        base_env["MISSION_LEASE_ID"] = lease_id
    base_env["MISSION_REQUIRE_SCORING_EVIDENCE"] = "0"
    if env_extra:
        base_env.update(env_extra)
    return subprocess.run(
        ["python3", str(MISSION_STATE_PY), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=base_env,
    )


def _get_lease(r) -> str:
    """init の stdout JSON から lease_id を取得する."""
    try:
        return json.loads(r.stdout)["lease_id"]
    except Exception:
        return "test-lease"


# ---------------------------------------------------------------------------
# AC-2: 並列 2 mission の回帰テスト
# ---------------------------------------------------------------------------


def test_parallel_init_creates_independent_state_files(tmp_path):
    """並列 2 mission が独立した sessions/<sid>.json を作成する."""
    base = "cc-df831137"
    sid_a = f"{base}-m824"
    sid_b = f"{base}-m825"

    r_a = _run_state(
        ["init", "Issue #824 の実装", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=sid_a,
    )
    assert r_a.returncode == 0, f"sid_a init stderr: {r_a.stderr}"

    r_b = _run_state(
        ["init", "Issue #825 の実装", "--issue-ref", "825", "--complexity", "Standard"],
        tmp_path, session_id=sid_b,
    )
    assert r_b.returncode == 0, f"sid_b init stderr: {r_b.stderr}"

    sf_a = tmp_path / ".mission-state" / "sessions" / f"{sid_a}.json"
    sf_b = tmp_path / ".mission-state" / "sessions" / f"{sid_b}.json"
    assert sf_a.exists(), f"sid_a state file not created: {sid_a}"
    assert sf_b.exists(), f"sid_b state file not created: {sid_b}"

    s_a = json.loads(sf_a.read_text())
    s_b = json.loads(sf_b.read_text())
    assert s_a["session_id"] == sid_a
    assert s_b["session_id"] == sid_b
    assert s_a["issue_ref"] == "824"
    assert s_b["issue_ref"] == "825"


def test_parallel_independent_lease(tmp_path):
    """並列 2 mission の lease は互いに独立している (fencing_epoch が別々)."""
    base = "cc-df831137"
    sid_a = f"{base}-m824"
    sid_b = f"{base}-m825"

    # lease_id=None: MISSION_LEASE_ID を未設定にし、init が新しいトークンを自動生成する
    r_a = _run_state(
        ["init", "Issue #824", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=sid_a, lease_id=None,
    )
    r_b = _run_state(
        ["init", "Issue #825", "--issue-ref", "825", "--complexity", "Standard"],
        tmp_path, session_id=sid_b, lease_id=None,
    )
    assert r_a.returncode == 0, f"sid_a init stderr: {r_a.stderr}"
    assert r_b.returncode == 0, f"sid_b init stderr: {r_b.stderr}"

    lease_a = _get_lease(r_a)
    lease_b = _get_lease(r_b)
    # 各 init は別々の lease_id を生成する (secrets.token_hex(16) で独立生成)
    assert lease_a != "test-lease", f"lease_a は自動生成されるべき: {lease_a!r}"
    assert lease_b != "test-lease", f"lease_b は自動生成されるべき: {lease_b!r}"
    assert lease_a != lease_b, "並列 session の lease_id は独立して生成される"


def test_parallel_mark_passes_only_finishes_own_session(tmp_path):
    """片方が mark-passes しても、もう一方の state は loop_active のまま."""
    base = "cc-df831137"
    sid_a = f"{base}-m824"
    sid_b = f"{base}-m825"

    r_a = _run_state(
        ["init", "Issue #824", "--issue-ref", "824", "--complexity", "Standard",
         "--artifact-applicability", "not-applicable"],
        tmp_path, session_id=sid_a,
    )
    r_b = _run_state(
        ["init", "Issue #825", "--issue-ref", "825", "--complexity", "Standard",
         "--artifact-applicability", "not-applicable"],
        tmp_path, session_id=sid_b,
    )
    assert r_a.returncode == 0
    assert r_b.returncode == 0

    lease_a = _get_lease(r_a)

    # sid_a に push-score を積み mark-passes できる状態にする
    archive = tmp_path / ".mission-state" / "archive"
    review_items = json.loads(_ITEMS)
    review_items.pop("reviewer_consensus")
    _, ref, claim = write_canonical_review_aggregate(
        tmp_path, [canonical_review(review_items)], name_prefix="fixture",
    )
    score = archive / "fixture-score.json"
    score.write_text(json.dumps({
        "items": claim["items"], "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"], "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {"score_source": "scoring-json", "review_evidence_ref": ref,
                             "revision_scope": ref["revision_scope"]},
    }))
    r_push = _run_state(["push-score", "--iteration", "1", "--scoring-json", str(score)], tmp_path, session_id=sid_a, lease_id=lease_a)
    assert r_push.returncode == 0, f"push-score stderr: {r_push.stderr}"

    # task_profile / specialists_decision を set (mark-passes に必要)
    sf_a = tmp_path / ".mission-state" / "sessions" / f"{sid_a}.json"
    data = json.loads(sf_a.read_text())
    data["task_profile"] = {"primary": "test"}
    data["specialists_decision"] = {"policy": "fallback", "action": "continue-core"}
    sf_a.write_text(json.dumps(data))

    r_mp = _run_state(
        ["mark-passes"],
        tmp_path, session_id=sid_a, lease_id=lease_a,
    )
    assert r_mp.returncode == 0, f"mark-passes stderr: {r_mp.stderr}"

    # sid_a は passes=true
    s_a = json.loads(sf_a.read_text())
    assert s_a["passes"] is True, "sid_a should have passes=true after mark-passes"

    # sid_b は loop_active=true のまま (影響を受けない)
    sf_b = tmp_path / ".mission-state" / "sessions" / f"{sid_b}.json"
    s_b = json.loads(sf_b.read_text())
    assert s_b["loop_active"] is True, "sid_b should still be loop_active=true"
    assert s_b["passes"] is False, "sid_b should remain passes=false"


def test_stop_guard_blocks_unfinished_not_passed(tmp_path):
    """stop-guard: 未完了 session は block、passed 済み session は通過させる.

    AC-2 の中核: 片方が mark-passes した後、unfinished の方は block され、
    passed の方はループ継続を強制されない。
    """
    base = "cc-df831137"
    sid_a = f"{base}-m824"  # 完了する session
    sid_b = f"{base}-m825"  # 未完了のまま残す session

    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)

    def _write(sid, *, loop_active, passes, issue_ref=None):
        data = {
            "session_id": sid,
            "loop_active": loop_active,
            "passes": passes,
            "halt_reason": "",
            "mission": f"mission for {sid}",
            "issue_ref": issue_ref,
            "project_root": str(tmp_path),
            "iteration": 2,
            "threshold": 4.0,
            "score_history": [{"composite": 4.5}],
            "pid": os.getpid(),
        }
        (sd / f"{sid}.json").write_text(json.dumps(data))

    # sid_a: 完了済み
    _write(sid_a, loop_active=False, passes=True, issue_ref="824")
    # sid_b: 未達
    _write(sid_b, loop_active=True, passes=False, issue_ref="825")

    # --- sid_b (未完了) は block される ---
    r_b = _run_hook(tmp_path, sid_b)
    assert "block" in r_b.stdout, (
        f"未完了 session {sid_b} は block されるべき: stdout={r_b.stdout!r}"
    )

    # --- sid_a (完了済み) は block されない ---
    r_a = _run_hook(tmp_path, sid_a)
    assert "block" not in r_a.stdout, (
        f"完了済み session {sid_a} は block されてはいけない: stdout={r_a.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC-3: stop-guard block メッセージに session_id / issue_ref が含まれる
# ---------------------------------------------------------------------------


def test_stop_guard_block_message_includes_session_id(tmp_path):
    """stop-guard の block 理由に session_id が含まれる."""
    sid = "cc-df831137-m825"
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    data = {
        "session_id": sid,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "mission": "parallel test mission",
        "issue_ref": "825",
        "project_root": str(tmp_path),
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "pid": os.getpid(),
    }
    (sd / f"{sid}.json").write_text(json.dumps(data))

    r = _run_hook(tmp_path, sid)
    assert "block" in r.stdout, f"未達なので block されるべき: {r.stdout!r}"
    reason = json.loads(r.stdout).get("reason", "")
    assert sid in reason, (
        f"block reason に session_id={sid!r} が含まれるべき: reason={reason!r}"
    )


def test_stop_guard_block_message_includes_issue_ref(tmp_path):
    """stop-guard の block 理由に issue_ref が含まれる."""
    sid = "cc-df831137-m825"
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    data = {
        "session_id": sid,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "mission": "parallel test mission",
        "issue_ref": "825",
        "project_root": str(tmp_path),
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "pid": os.getpid(),
    }
    (sd / f"{sid}.json").write_text(json.dumps(data))

    r = _run_hook(tmp_path, sid)
    assert "block" in r.stdout
    reason = json.loads(r.stdout).get("reason", "")
    assert "825" in reason, (
        f"block reason に issue_ref=825 が含まれるべき: reason={reason!r}"
    )


def test_stop_guard_block_message_no_issue_ref_still_shows_sid(tmp_path):
    """issue_ref がない場合でも session_id は block reason に含まれる."""
    sid = "cc-df831137-m825"
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    data = {
        "session_id": sid,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "mission": "no issue_ref mission",
        "project_root": str(tmp_path),
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "pid": os.getpid(),
    }
    (sd / f"{sid}.json").write_text(json.dumps(data))

    r = _run_hook(tmp_path, sid)
    assert "block" in r.stdout
    reason = json.loads(r.stdout).get("reason", "")
    assert sid in reason, (
        f"issue_ref なしでも session_id={sid!r} が reason に含まれるべき: reason={reason!r}"
    )


def test_stop_guard_multi_session_pending_all_shown(tmp_path):
    """並列 2 session が未達の場合、block reason に両方の session_id が含まれる."""
    base = "cc-df831137"
    sid_a = f"{base}-m824"
    sid_b = f"{base}-m825"

    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)

    for sid, iref in [(sid_a, "824"), (sid_b, "825")]:
        data = {
            "session_id": sid,
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "mission": f"mission {sid}",
            "issue_ref": iref,
            "project_root": str(tmp_path),
            "iteration": 1,
            "threshold": 4.0,
            "score_history": [],
            "pid": os.getpid(),
        }
        (sd / f"{sid}.json").write_text(json.dumps(data))

    # sid_a で hook を実行したとき、block reason に両方の未達 session が示される
    r = _run_hook(tmp_path, sid_a)
    assert "block" in r.stdout
    reason = json.loads(r.stdout).get("reason", "")
    # 少なくとも自 session は含まれる
    assert sid_a in reason, f"blocking session {sid_a} が reason に含まれるべき: {reason!r}"
    # 他の未達 session も示される (breakdown)
    assert sid_b in reason, (
        f"未達 session {sid_b} も reason に含まれるべき (breakdown): {reason!r}"
    )


# ---------------------------------------------------------------------------
# AC-4: 並列命名規約 (<base>-m<issue>) での重複 init 警告
# ---------------------------------------------------------------------------


def test_parallel_naming_dup_issue_ref_warns(tmp_path):
    """並列命名規約 (<base>-m<issue>) を使った同一 issue_ref の重複 init は警告が出る.

    実務ユースケース: オーケストレーターが誤って同一 Issue に 2 つの論理セッションを init しようとした場合。
    """
    base = "cc-df831137"

    # 1st logical session: base-m824 で issue_ref=824 を init
    r_a = _run_state(
        ["init", "Issue #824 実装 (first)", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m824",
    )
    assert r_a.returncode == 0, f"first init stderr: {r_a.stderr}"

    # 2nd logical session: base-m824-retry (同一 issue 824) — 重複のはず
    r_b = _run_state(
        ["init", "Issue #824 実装 (second)", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m824-retry",
    )
    assert r_b.returncode == 0, f"second init should succeed (warn not reject): {r_b.stderr}"

    # 警告が stderr に出ること
    assert "warn" in r_b.stderr.lower() or "warning" in r_b.stderr.lower(), (
        f"同一 issue_ref の重複 init で警告が出るべき: stderr={r_b.stderr!r}"
    )
    assert "824" in r_b.stderr or "issue_ref" in r_b.stderr.lower(), (
        f"警告に issue_ref=824 が含まれるべき: stderr={r_b.stderr!r}"
    )


def test_parallel_naming_different_issue_no_warn(tmp_path):
    """並列命名規約で異なる issue を指定した場合は警告が出ない (正常な並列利用)."""
    base = "cc-df831137"

    r_a = _run_state(
        ["init", "Issue #824 実装", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m824",
    )
    assert r_a.returncode == 0

    r_b = _run_state(
        ["init", "Issue #825 実装", "--issue-ref", "825", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m825",
    )
    assert r_b.returncode == 0

    # issue_ref 関連の警告が出ないこと (別番号は重複でない)
    warn_lines = [
        ln for ln in r_b.stderr.splitlines()
        if ("warn" in ln.lower() or "warning" in ln.lower()) and "issue_ref" in ln.lower()
    ]
    assert warn_lines == [], (
        f"異なる issue_ref で誤った重複警告が出てはいけない: {r_b.stderr!r}"
    )


def test_parallel_naming_passed_session_no_warn_on_new_init(tmp_path):
    """完了済み session と同一 issue_ref を新規 init しても警告が出ない (#296 後方互換)."""
    base = "cc-df831137"

    r_a = _run_state(
        ["init", "Issue #824 実装", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m824",
    )
    assert r_a.returncode == 0

    # 完了済みにする
    sf_a = tmp_path / ".mission-state" / "sessions" / f"{base}-m824.json"
    data = json.loads(sf_a.read_text())
    data["passes"] = True
    data["loop_active"] = False
    sf_a.write_text(json.dumps(data))

    # 完了済みと同一 issue_ref で新規 session — 警告が出ないはず
    r_b = _run_state(
        ["init", "Issue #824 再実装", "--issue-ref", "824", "--complexity", "Standard"],
        tmp_path, session_id=f"{base}-m824-v2",
    )
    assert r_b.returncode == 0

    warn_lines = [
        ln for ln in r_b.stderr.splitlines()
        if ("warn" in ln.lower() or "warning" in ln.lower()) and "issue_ref" in ln.lower()
    ]
    assert warn_lines == [], (
        f"完了済み session に対して重複警告が出てはいけない: {r_b.stderr!r}"
    )
