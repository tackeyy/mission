"""Issue #318: resolve-archive で frozen snapshot と削除済み worktree 由来 record を解消できない.

AC:
- [A1] 系統A: live session が terminal な frozen snapshot へ --frozen-snapshot フラグで resolution を付与できる
- [A2] 系統A: live session が active（loop_active=true）の場合は --frozen-snapshot でも拒否する
- [B1] 系統B: cwd 配下 worktree の project_root を持つ archive record を cwd から解消できる
- [B2] 系統B: cwd 配下でない project_root は引き続き拒否する
- [C1] audit: resolution 済み archive record が stale-active-no-score から除外される
- [G] 全 Gate 通過（plugins_in_sync / artifact_hygiene / vendor_fingerprint 等は別ファイルで確認）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_halted(path: Path, **overrides) -> None:
    """Write a minimal terminal halted state record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    project_root = overrides.pop("project_root", str(path.parents[2]))
    state = {
        "mission": "318 frozen snapshot test",
        "mission_id": "abc318456789",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": False,
        "passes": False,
        "halt_reason": "threshold gate remains unmet after max iterations",
        "halt_category": "stagnation",
        "started_at": "2026-06-18T00:00:00Z",
        "updated_at": "2026-06-18T00:10:00Z",
        "project_root": project_root,
        "session_id": "sess-frozen-318",
        "agent": "codex",
        "schema_version": 2,
    }
    state.update(overrides)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _write_active_snapshot(path: Path, session_id: str, project_root: str) -> None:
    """Write a mid-flight (loop_active=true) frozen snapshot to simulate a running session capture.

    These records have loop_active=True but NO halt_reason (they are captured mid-flight,
    before any halt was issued). This is the "active-shaped" form that causes stale-active-no-score
    in audit and cannot be resolved by normal resolve-archive (requires --frozen-snapshot).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "318 active snapshot test",
        "mission_id": "abc318active",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],   # no scoring checkpoint
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",  # very old → will be stale
        "project_root": project_root,
        "session_id": session_id,
        "agent": "codex",
        "schema_version": 2,
    }
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _run(args, *, cwd, env_extra=None):
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith("MISSION_")
                and k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")}
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


def _resolve(cwd, path, status="resolved", extra=None):
    """Call resolve-archive with common defaults."""
    args = ["resolve-archive", "--path", str(path), "--status", status, "--json"]
    if extra:
        args += list(extra)
    return _run(args, cwd=cwd)


# ---------------------------------------------------------------------------
# 系統A: --frozen-snapshot フラグ / live session が terminal な frozen snapshot
# ---------------------------------------------------------------------------

def test_frozen_snapshot_allows_active_record_when_live_session_absent(tmp_path):
    """[A1] live session が存在しない場合、--frozen-snapshot でactive-shaped archive record を解消できる."""
    session_id = "sess-frozen-absent"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-41e863af.json"
    _write_active_snapshot(af, session_id=session_id, project_root=str(tmp_path))

    # sessions/<sid>.json は存在しない（削除済み or 元から無い）
    assert not (tmp_path / ".mission-state" / "sessions" / f"{session_id}.json").exists()

    r = _resolve(
        tmp_path,
        f".mission-state/archive/state-{session_id}-41e863af.json",
        status="resolved",
        extra=["--frozen-snapshot", "--note", "live session absent; frozen snapshot"],
    )
    assert r.returncode == 0, r.stderr

    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert out["resolution_status"] == "resolved"

    data = json.loads(af.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "resolved"
    assert "resolution_decided_at" in data
    # identity must not change; halt_reason stays empty (mid-flight snapshot)
    assert data["halt_reason"] == ""
    assert data["session_id"] == session_id


def test_frozen_snapshot_allows_active_record_when_live_session_terminal(tmp_path):
    """[A1] live session が terminal（loop_active=false）の場合、--frozen-snapshot で解消できる."""
    session_id = "sess-frozen-terminal"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-57cd542d.json"
    _write_active_snapshot(af, session_id=session_id, project_root=str(tmp_path))

    # Create a terminal live session
    live = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    live.parent.mkdir(parents=True, exist_ok=True)
    live_state = {
        "mission": "318 terminal live session",
        "mission_id": "abc318live",
        "session_id": session_id,
        "loop_active": False,
        "passes": True,
        "halt_reason": "",
        "project_root": str(tmp_path),
        "resolution_status": "resolved",
        "schema_version": 2,
    }
    live.write_text(json.dumps(live_state), encoding="utf-8")

    r = _resolve(
        tmp_path,
        f".mission-state/archive/state-{session_id}-57cd542d.json",
        status="resolved",
        extra=["--frozen-snapshot", "--note", "live session is terminal"],
    )
    assert r.returncode == 0, r.stderr

    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert out["resolution_status"] == "resolved"


def test_frozen_snapshot_rejects_when_live_session_still_active(tmp_path):
    """[A2] live session が loop_active=true の場合、--frozen-snapshot でも拒否される."""
    session_id = "sess-frozen-live-active"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-deadbeef.json"
    _write_active_snapshot(af, session_id=session_id, project_root=str(tmp_path))

    # Create an active live session
    live = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    live.parent.mkdir(parents=True, exist_ok=True)
    live_state = {
        "mission": "318 active live session",
        "mission_id": "abc318activelive",
        "session_id": session_id,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "project_root": str(tmp_path),
        "schema_version": 2,
    }
    live.write_text(json.dumps(live_state), encoding="utf-8")

    r = _resolve(
        tmp_path,
        f".mission-state/archive/state-{session_id}-deadbeef.json",
        status="resolved",
        extra=["--frozen-snapshot"],
    )
    assert r.returncode != 0
    # Should mention that live session is active
    assert "active" in r.stderr.lower() or "live" in r.stderr.lower()


def test_frozen_snapshot_flag_rejected_for_sessions_path(tmp_path):
    """[A2] --frozen-snapshot は sessions/ 配下には適用できない（archive/ のみ有効）."""
    session_id = "sess-in-sessions"
    sf = tmp_path / ".mission-state" / "sessions" / f"{session_id}.json"
    # Create an active-shaped (mid-flight) record in sessions/
    sf.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "318 sessions dir test",
        "mission_id": "abc318sess",
        "session_id": session_id,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "project_root": str(tmp_path),
        "schema_version": 2,
    }
    sf.write_text(json.dumps(state), encoding="utf-8")

    r = _resolve(
        tmp_path,
        f".mission-state/sessions/{session_id}.json",
        status="resolved",
        extra=["--frozen-snapshot"],
    )
    assert r.returncode != 0
    # Should mention archive or sessions restriction
    assert "archive" in r.stderr.lower() or "sessions" in r.stderr.lower()


def test_frozen_snapshot_preserves_backward_compat_without_flag(tmp_path):
    """[後方互換] --frozen-snapshot なしの active-shaped archive record は従来どおり拒否される."""
    session_id = "sess-active-compat"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-abcdef01.json"
    _write_active_snapshot(af, session_id=session_id, project_root=str(tmp_path))

    # Without --frozen-snapshot, should still reject active-shaped records
    # (loop_active=True fails the active check, or halt_reason="" fails the halt_reason check)
    r = _resolve(
        tmp_path,
        f".mission-state/archive/state-{session_id}-abcdef01.json",
        status="resolved",
    )
    assert r.returncode != 0
    assert "active" in r.stderr.lower() or "loop_active" in r.stderr or "halt" in r.stderr.lower()


# ---------------------------------------------------------------------------
# 系統B: worktree 由来 project_root の許容
# ---------------------------------------------------------------------------

def test_worktree_project_root_allowed_from_main_checkout(tmp_path):
    """[B1] cwd 配下の .worktrees/<name> が project_root の archive record を cwd から解消できる."""
    # Simulate: main checkout = tmp_path, worktree = tmp_path/.worktrees/583-gitleaks-range
    worktree_path = tmp_path / ".worktrees" / "583-gitleaks-range"
    worktree_path.mkdir(parents=True)

    af = tmp_path / ".mission-state" / "archive" / "state-sess-worktree-0853988d.json"
    _write_halted(
        af,
        session_id="sess-worktree-b1",
        project_root=str(worktree_path),  # points to deleted worktree subdir
    )

    # cwd is tmp_path (main checkout)
    r = _resolve(
        tmp_path,
        ".mission-state/archive/state-sess-worktree-0853988d.json",
        status="superseded",
        extra=["--note", "worktree deleted; resolved from main checkout"],
    )
    assert r.returncode == 0, r.stderr

    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert out["resolution_status"] == "superseded"

    data = json.loads(af.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "superseded"
    assert data["halt_reason"] == "threshold gate remains unmet after max iterations"


def test_worktree_project_root_rejected_for_unrelated_project(tmp_path):
    """[B2] cwd 配下でない project_root は引き続き拒否される."""
    # Simulate: record's project_root is a completely separate directory
    other_root = tmp_path.parent / "other-project"

    af = tmp_path / ".mission-state" / "archive" / "state-sess-other-deadbeef.json"
    _write_halted(
        af,
        session_id="sess-other-b2",
        project_root=str(other_root),
    )

    r = _resolve(
        tmp_path,
        ".mission-state/archive/state-sess-other-deadbeef.json",
        status="resolved",
    )
    assert r.returncode != 0
    assert "project" in r.stderr.lower()


def test_worktree_project_root_allowed_for_sessions_path_too(tmp_path):
    """[B1] sessions/ 配下でも cwd 配下 worktree の project_root は許容される."""
    worktree_path = tmp_path / ".worktrees" / "999-some-worktree"
    worktree_path.mkdir(parents=True)

    sf = tmp_path / ".mission-state" / "sessions" / "sess-worktree-sess.json"
    _write_halted(
        sf,
        session_id="sess-worktree-sess",
        project_root=str(worktree_path),
    )

    r = _resolve(
        tmp_path,
        ".mission-state/sessions/sess-worktree-sess.json",
        status="closed",
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "closed"


# ---------------------------------------------------------------------------
# 系統C: audit stale-active-no-score から resolution 済み record を除外
# ---------------------------------------------------------------------------

def test_audit_excludes_resolved_stale_active_no_score(tmp_path):
    """[C1] resolution 済みの active-shaped archive record が stale-active-no-score から除外される."""
    session_id = "sess-stale-resolved"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-cafebabe.json"

    # Write an active-shaped archive record that would normally appear in stale-active-no-score.
    # Active-shaped: loop_active=True, halt_reason="" (mid-flight snapshot), no score, very old timestamp.
    af.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "318 stale active no score resolved",
        "mission_id": "abc318stale",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],   # no scoring checkpoint → stale-active-no-score without resolution
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",   # very old → will be "stale"
        "project_root": str(tmp_path),
        "session_id": session_id,
        "agent": "codex",
        "schema_version": 2,
        # Already has resolution (as if resolved via --frozen-snapshot earlier)
        "resolution_status": "resolved",
        "resolution_decided_at": "2026-08-01T00:00:00Z",
        "resolution_note": "frozen snapshot; live session terminal",
    }
    af.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # Run audit — record should NOT appear in stale_active_no_score
    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-01-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)

    stale_paths = [
        r.get("path", "") for r in audit.get("stale_active_no_score_sessions", [])
    ]
    assert str(af) not in stale_paths, (
        f"Resolved record should not appear in stale_active_no_score. "
        f"Got: {stale_paths}"
    )

    # stale_active_no_score_count should be 0 (only one record in this tmp_path)
    assert audit.get("stale_active_no_score_count", 0) == 0, (
        f"Expected stale_active_no_score_count=0, got {audit.get('stale_active_no_score_count')}"
    )


def test_audit_still_flags_unresolved_stale_active_no_score(tmp_path):
    """[C1] resolution がない active-shaped record は引き続き stale-active-no-score に計上される."""
    session_id = "sess-stale-unresolved"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-baadf00d.json"

    af.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "318 stale active no score unresolved",
        "mission_id": "abc318staleunres",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",
        "project_root": str(tmp_path),
        "session_id": session_id,
        "agent": "codex",
        "schema_version": 2,
        # No resolution_status
    }
    af.write_text(json.dumps(state, indent=2), encoding="utf-8")

    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-01-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)

    # Should still appear in stale_active_no_score
    assert audit.get("stale_active_no_score_count", 0) >= 1, (
        f"Unresolved record should appear in stale_active_no_score. "
        f"Got count={audit.get('stale_active_no_score_count')}"
    )


def test_audit_mixed_resolved_and_unresolved_stale_active_no_score(tmp_path):
    """[C1] resolved と unresolved が混在する場合、resolved のみが除外される."""
    sessions_dir = tmp_path / ".mission-state" / "archive"
    sessions_dir.mkdir(parents=True)

    # Resolved (should be excluded)
    af_resolved = sessions_dir / "state-sess-res-cafecafe.json"
    state_resolved = {
        "mission": "318 resolved stale",
        "mission_id": "abc318res",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",
        "project_root": str(tmp_path),
        "session_id": "sess-res",
        "agent": "codex",
        "schema_version": 2,
        "resolution_status": "resolved",
        "resolution_decided_at": "2026-08-01T00:00:00Z",
    }
    af_resolved.write_text(json.dumps(state_resolved, indent=2), encoding="utf-8")

    # Unresolved (should still be flagged)
    af_unresolved = sessions_dir / "state-sess-unres-deadcafe.json"
    state_unresolved = {
        "mission": "318 unresolved stale",
        "mission_id": "abc318unres",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",
        "project_root": str(tmp_path),
        "session_id": "sess-unres",
        "agent": "codex",
        "schema_version": 2,
        # No resolution_status
    }
    af_unresolved.write_text(json.dumps(state_unresolved, indent=2), encoding="utf-8")

    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-01-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)

    stale_paths = [
        r.get("path", "") for r in audit.get("stale_active_no_score_sessions", [])
    ]

    # Resolved should NOT be in the list
    assert str(af_resolved) not in stale_paths, (
        f"Resolved record should not appear in stale_active_no_score. Got: {stale_paths}"
    )
    # Unresolved SHOULD be in the list
    assert str(af_unresolved) in stale_paths or audit.get("stale_active_no_score_count", 0) >= 1, (
        f"Unresolved record should appear in stale_active_no_score. Got: {stale_paths}"
    )
    assert audit.get("stale_active_no_score_count", 0) == 1, (
        f"Expected count=1 (only unresolved), got {audit.get('stale_active_no_score_count')}"
    )


# ---------------------------------------------------------------------------
# end-to-end: resolve frozen snapshot then verify audit exclusion
# ---------------------------------------------------------------------------

def test_e2e_frozen_snapshot_resolve_then_audit_clean(tmp_path):
    """[A1+C1] --frozen-snapshot で解消後、audit の stale-active-no-score に現れない統合テスト."""
    session_id = "sess-e2e-frozen"
    af = tmp_path / ".mission-state" / "archive" / f"state-{session_id}-e2e00001.json"

    # Active-shaped archive record: loop_active=True, halt_reason="" (mid-flight), no score, old timestamp
    # → would appear as stale-active-no-score without resolution
    af.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "318 e2e frozen snapshot",
        "mission_id": "abc318e2e",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",     # mid-flight: no halt yet
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:10:00Z",
        "project_root": str(tmp_path),
        "session_id": session_id,
        "agent": "cc",
        "schema_version": 2,
    }
    af.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # sessions/<sid>.json does not exist (already gone)
    assert not (tmp_path / ".mission-state" / "sessions" / f"{session_id}.json").exists()

    # Step 1: resolve via --frozen-snapshot
    r = _resolve(
        tmp_path,
        f".mission-state/archive/state-{session_id}-e2e00001.json",
        status="resolved",
        extra=["--frozen-snapshot", "--owner-issue", "318", "--note", "e2e test"],
    )
    assert r.returncode == 0, r.stderr

    # Step 2: verify resolution written
    data = json.loads(af.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "resolved"
    assert data.get("resolution_owner_issue") == "318"

    # Step 3: run audit — should not appear in stale-active-no-score
    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-01-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)

    assert audit.get("stale_active_no_score_count", 0) == 0, (
        f"After resolution, stale_active_no_score_count should be 0. "
        f"Got: {audit.get('stale_active_no_score_count')}"
    )
