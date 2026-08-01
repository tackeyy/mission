"""Issue #301: terminal archive state へ監査可能な resolution metadata を付与できない.

resolve-archive サブコマンドのテスト:
- terminal halted record への resolution metadata の付与
- active / 非halt / 範囲外 / symlink / immutable generation bundle の fail-closed 拒否
- halt_reason / halt_category / identity / events の不変保持
- resolution history の append
- mission-audit.py での P1 除外の統合確認
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
        "mission": "resolve archive test",
        "mission_id": "abc123456789",
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
        "session_id": "sess-halted",
        "agent": "codex",
        "schema_version": 2,
    }
    state.update(overrides)
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
# basic: session record
# ---------------------------------------------------------------------------

def test_resolve_archive_session_record(tmp_path):
    """セッションファイルへ resolution metadata を付与できる."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    r = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json", status="resolved")
    assert r.returncode == 0, r.stderr

    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert out["resolution_status"] == "resolved"
    assert "resolution_decided_at" in out

    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "resolved"
    assert "resolution_decided_at" in data

    # halt information must not change
    assert data["halt_reason"] == "threshold gate remains unmet after max iterations"
    assert data["halt_category"] == "stagnation"
    assert data["session_id"] == "sess-halted"
    assert data["mission_id"] == "abc123456789"


def test_resolve_archive_flat_archive_record(tmp_path):
    """archive/state-*.json へ resolution metadata を付与できる."""
    af = tmp_path / ".mission-state" / "archive" / "state-sess-halted-abc12345.json"
    _write_halted(af, project_root=str(tmp_path))

    r = _resolve(tmp_path, ".mission-state/archive/state-sess-halted-abc12345.json",
                 status="superseded")
    assert r.returncode == 0, r.stderr

    data = json.loads(af.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "superseded"
    assert data["halt_reason"] == "threshold gate remains unmet after max iterations"
    assert data["halt_category"] == "stagnation"


def test_resolve_archive_closed_status(tmp_path):
    """status='closed' が受理される."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    r = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json", status="closed")
    assert r.returncode == 0, r.stderr

    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "closed"


# ---------------------------------------------------------------------------
# optional metadata fields
# ---------------------------------------------------------------------------

def test_resolve_archive_with_all_optional_fields(tmp_path):
    """owner_issue / evidence_url / note が記録される."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    r = _resolve(
        tmp_path,
        ".mission-state/sessions/sess-halted.json",
        status="resolved",
        extra=[
            "--owner-issue", "301",
            "--evidence-url", "https://github.com/tackeyy/mission/pull/42",
            "--note", "replaced by follow-up session",
        ],
    )
    assert r.returncode == 0, r.stderr

    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["resolution_owner_issue"] == "301"
    assert data["resolution_evidence_url"] == "https://github.com/tackeyy/mission/pull/42"
    assert data["resolution_note"] == "replaced by follow-up session"


# ---------------------------------------------------------------------------
# resolution history (re-resolve appends)
# ---------------------------------------------------------------------------

def test_resolve_archive_history_append_on_reresolve(tmp_path):
    """既に resolution がある record に再付与すると履歴へ append される."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    # 1st resolution
    r1 = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json",
                  status="superseded",
                  extra=["--note", "first resolution"])
    assert r1.returncode == 0, r1.stderr

    # 2nd resolution
    r2 = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json",
                  status="resolved",
                  extra=["--note", "second resolution"])
    assert r2.returncode == 0, r2.stderr

    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["resolution_status"] == "resolved"
    assert data["resolution_note"] == "second resolution"

    history = data.get("resolution_history", [])
    assert len(history) == 1
    assert history[0]["resolution_status"] == "superseded"
    assert history[0]["resolution_note"] == "first resolution"

    # identity / halt info must not change throughout
    assert data["halt_reason"] == "threshold gate remains unmet after max iterations"
    assert data["session_id"] == "sess-halted"


# ---------------------------------------------------------------------------
# fail-closed: active record
# ---------------------------------------------------------------------------

def test_resolve_archive_rejects_active_record(tmp_path):
    """loop_active=true の record は拒否される."""
    sf = tmp_path / ".mission-state" / "sessions" / "active-sess.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "x", "mission_id": "aaa", "session_id": "active-sess",
        "loop_active": True, "passes": False, "halt_reason": "",
        "project_root": str(tmp_path),
        "started_at": "2026-06-18T00:00:00Z", "updated_at": "2026-06-18T00:01:00Z",
        "schema_version": 2,
    }
    sf.write_text(json.dumps(state), encoding="utf-8")

    r = _resolve(tmp_path, ".mission-state/sessions/active-sess.json", status="resolved")
    assert r.returncode != 0
    assert "active" in r.stderr.lower() or "loop_active" in r.stderr


def test_resolve_archive_rejects_passes_true_record(tmp_path):
    """passes=true の record は拒否される."""
    sf = tmp_path / ".mission-state" / "sessions" / "passed-sess.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "x", "mission_id": "aaa", "session_id": "passed-sess",
        "loop_active": False, "passes": True, "halt_reason": "",
        "project_root": str(tmp_path),
        "score_history": [{"composite": 5.0, "min_item": 5.0, "iteration": 1, "items": {}}],
        "started_at": "2026-06-18T00:00:00Z", "updated_at": "2026-06-18T00:01:00Z",
        "schema_version": 2,
    }
    sf.write_text(json.dumps(state), encoding="utf-8")

    r = _resolve(tmp_path, ".mission-state/sessions/passed-sess.json", status="resolved")
    assert r.returncode != 0
    assert "passes" in r.stderr.lower() or "halt" in r.stderr.lower()


def test_resolve_archive_rejects_no_halt_reason(tmp_path):
    """halt_reason が空文字列の record は拒否される."""
    sf = tmp_path / ".mission-state" / "sessions" / "no-halt.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "x", "mission_id": "aaa", "session_id": "no-halt",
        "loop_active": False, "passes": False, "halt_reason": "",
        "project_root": str(tmp_path),
        "started_at": "2026-06-18T00:00:00Z", "updated_at": "2026-06-18T00:01:00Z",
        "schema_version": 2,
    }
    sf.write_text(json.dumps(state), encoding="utf-8")

    r = _resolve(tmp_path, ".mission-state/sessions/no-halt.json", status="resolved")
    assert r.returncode != 0
    assert "halt" in r.stderr.lower()


# ---------------------------------------------------------------------------
# fail-closed: path outside project root
# ---------------------------------------------------------------------------

def test_resolve_archive_rejects_path_outside_project(tmp_path):
    """cwd の .mission-state 外のパスは拒否される."""
    outside = tmp_path / "other" / ".mission-state" / "sessions" / "x.json"
    _write_halted(outside, project_root=str(tmp_path / "other"))

    # cwd is tmp_path, but target is in tmp_path/other/.mission-state/
    r = _resolve(tmp_path, str(outside), status="resolved")
    assert r.returncode != 0
    # error about path being outside
    assert "outside" in r.stderr.lower() or ".mission-state" in r.stderr.lower() or "project" in r.stderr.lower()


def test_resolve_archive_rejects_path_traversal(tmp_path):
    """../.. パス traversal は拒否される."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    # Attempt .. escape from within .mission-state
    traversal = ".mission-state/sessions/../../.mission-state/sessions/sess-halted.json"
    r = _resolve(tmp_path, traversal, status="resolved")
    # Should either succeed (if normalized to valid path) or reject for .. escape.
    # Key constraint: if it normalizes away, it might succeed because the normalized
    # path is still within .mission-state. This is actually OK behavior - but the
    # record must only be modified if the normalized path is valid.
    # The primary concern is escaping UP and OUT of .mission-state.
    # A traversal that stays inside is acceptable; one that leaves is not.
    outside_traversal = ".mission-state/../../../etc/passwd"
    r2 = _resolve(tmp_path, outside_traversal, status="resolved")
    assert r2.returncode != 0


def test_resolve_archive_rejects_path_with_symlink(tmp_path):
    """パス上の symlink は拒否される."""
    real_dir = tmp_path / "real_state" / ".mission-state" / "sessions"
    real_dir.mkdir(parents=True)
    sf = real_dir / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path / "real_state"))

    # Create symlink: .mission-state points to real directory
    link_parent = tmp_path / "linked_project"
    link_parent.mkdir()
    link = link_parent / ".mission-state"
    link.symlink_to(tmp_path / "real_state" / ".mission-state")

    r = _resolve(link_parent, ".mission-state/sessions/sess-halted.json", status="resolved")
    assert r.returncode != 0
    assert "symlink" in r.stderr.lower()


# ---------------------------------------------------------------------------
# fail-closed: immutable worktree generation bundle
# ---------------------------------------------------------------------------

def test_resolve_archive_rejects_immutable_generation_record(tmp_path):
    """archive/worktree-*/generations/<gen>/ 内の record は拒否される."""
    bundle = tmp_path / ".mission-state" / "archive" / "worktree-feat-123"
    gen_dir = bundle / "generations" / "abc123def456"
    sessions_dir = gen_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    sf = sessions_dir / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    r = _resolve(
        tmp_path,
        ".mission-state/archive/worktree-feat-123/generations/abc123def456/sessions/sess-halted.json",
        status="resolved",
    )
    assert r.returncode != 0
    assert "immutable" in r.stderr.lower() or "generation" in r.stderr.lower()


# ---------------------------------------------------------------------------
# fail-closed: different project record
# ---------------------------------------------------------------------------

def test_resolve_archive_rejects_different_project_record(tmp_path):
    """project_root が cwd またはその配下でない record は拒否される (#318: cwd 配下 worktree は許容)."""
    sf = tmp_path / ".mission-state" / "sessions" / "foreign.json"
    # Use a sibling directory (parent of tmp_path) — truly unrelated, not a worktree of cwd
    other_project = tmp_path.parent / "other_project"
    _write_halted(sf, project_root=str(other_project), session_id="foreign")

    r = _resolve(tmp_path, ".mission-state/sessions/foreign.json", status="resolved")
    assert r.returncode != 0
    assert "project" in r.stderr.lower()


# ---------------------------------------------------------------------------
# fail-closed: invalid status
# ---------------------------------------------------------------------------

def test_resolve_archive_rejects_invalid_status(tmp_path):
    """無効な status は拒否される."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(sf, project_root=str(tmp_path))

    r = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json", status="invalid-status")
    assert r.returncode != 0
    assert "status" in r.stderr.lower() or "invalid" in r.stderr.lower()


# ---------------------------------------------------------------------------
# immutability: frozen fields must not change
# ---------------------------------------------------------------------------

def test_resolve_archive_does_not_change_frozen_fields(tmp_path):
    """resolution 付与後も halt_reason / halt_category / identity / events が不変."""
    sf = tmp_path / ".mission-state" / "sessions" / "sess-halted.json"
    _write_halted(
        sf,
        project_root=str(tmp_path),
        score_history=[
            {"iteration": 1, "composite": 3.5, "min_item": 3.0, "items": {}, "timestamp": "2026-06-18T00:05:00Z"}
        ],
        decisions=[{"step": "init", "choice": "Standard"}],
    )
    original = json.loads(sf.read_text(encoding="utf-8"))

    r = _resolve(tmp_path, ".mission-state/sessions/sess-halted.json", status="resolved")
    assert r.returncode == 0, r.stderr

    updated = json.loads(sf.read_text(encoding="utf-8"))

    for field in ("halt_reason", "halt_category", "session_id", "mission_id",
                  "mission", "started_at", "score_history", "decisions"):
        assert updated.get(field) == original.get(field), \
            f"field {field!r} was mutated: {original.get(field)!r} -> {updated.get(field)!r}"

    # resolution fields added
    assert updated["resolution_status"] == "resolved"
    assert "resolution_decided_at" in updated


# ---------------------------------------------------------------------------
# integration: mission-audit.py excludes resolved records from P1
# ---------------------------------------------------------------------------

def test_audit_excludes_resolved_halted_record_from_p1(tmp_path):
    """resolve-archive で resolved にした record が audit P1 halted-runs から除外される."""
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)

    # Actionable halt (not resolved)
    sf_actionable = sessions / "actionable.json"
    _write_halted(
        sf_actionable,
        session_id="actionable",
        project_root=str(tmp_path),
        halt_category="stagnation",
        halt_reason="no progress was made for three iterations",
        updated_at="2026-08-01T00:10:00Z",
    )

    # Halted record that will be resolved
    sf_resolved = sessions / "will-resolve.json"
    _write_halted(
        sf_resolved,
        session_id="will-resolve",
        project_root=str(tmp_path),
        halt_category="other",
        halt_reason="replaced by new implementation",
        updated_at="2026-08-01T00:11:00Z",
    )

    # Apply resolution via CLI
    r = _resolve(tmp_path, ".mission-state/sessions/will-resolve.json", status="resolved")
    assert r.returncode == 0, r.stderr

    # Run audit
    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-08-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)

    # actionable halt count = 1 (only actionable)
    assert audit["actionable_halt_count"] == 1
    # total halt count = 2 (both are halted records)
    assert audit["halt_count"] == 2

    finding_counts = audit.get("all_finding_code_counts", {})
    assert finding_counts.get("halted-runs", 0) == 1, \
        f"expected halted-runs=1, got {finding_counts}"


def test_audit_excludes_superseded_archived_record_from_p1(tmp_path):
    """archive/state-*.json に superseded 付与後、P1 から除外される."""
    af = tmp_path / ".mission-state" / "archive" / "state-sess-ab-abc12345.json"
    af.parent.mkdir(parents=True)
    _write_halted(
        af,
        session_id="sess-ab",
        project_root=str(tmp_path),
        halt_category="stagnation",
        halt_reason="stagnated",
        updated_at="2026-08-01T00:15:00Z",
    )

    # Apply resolution
    r = _resolve(tmp_path, ".mission-state/archive/state-sess-ab-abc12345.json",
                 status="superseded")
    assert r.returncode == 0, r.stderr

    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY),
         "--root", str(tmp_path), "--since", "2026-08-01", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit = json.loads(audit_result.stdout)
    finding_counts = audit.get("all_finding_code_counts", {})
    assert finding_counts.get("halted-runs", 0) == 0, \
        f"superseded record should not appear in P1, got {finding_counts}"
