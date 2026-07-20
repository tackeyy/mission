"""Issue #210: reusable, fail-closed audit/stats state snapshots."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"
STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"


def _write_state(root: Path, session_id: str, updated_at: str, **overrides: object) -> Path:
    path = root / ".mission-state" / "sessions" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": f"snapshot {session_id}",
        "mission_id": f"mission-{session_id}",
        "session_id": session_id,
        "project_root": str(root),
        "iteration": 1,
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
        "started_at": updated_at,
        "updated_at": updated_at,
        "score_history": [
            {"iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {}}
        ],
    }
    state.update(overrides)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def _run_audit(*args: str, cwd: Path, env: dict[str, str] | None = None):
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("MISSION_")}
    clean_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(AUDIT_PY), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=clean_env,
    )


def _snapshot(root: Path, path: Path, *extra: str, env: dict[str, str] | None = None):
    return _run_audit(
        "--root", str(root), "--snapshot-out", str(path),
        "--snapshot-ttl-sec", "3600", "--json", *extra, cwd=root, env=env,
    )


def _canonical_digest(document: dict) -> str:
    core = {key: value for key, value in document.items() if key != "content_digest"}
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rewrite_snapshot(path: Path, mutate) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    document["content_digest"] = _canonical_digest(document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_audit_snapshot_matches_direct_and_preserves_filter_before_dedupe(tmp_path):
    root = tmp_path / "root"
    live = _write_state(root, "same", "2026-07-21T12:00:00Z", passes=False,
                        loop_active=False, phase="halt", halt_reason="stopped")
    archived = root / ".mission-state" / "archive" / "state-old.json"
    archived.parent.mkdir(parents=True)
    old = json.loads(live.read_text(encoding="utf-8"))
    old.update({"passes": True, "halt_reason": "", "phase": "done",
                "updated_at": "2026-07-19T12:00:00Z"})
    archived.write_text(json.dumps(old) + "\n", encoding="utf-8")
    snapshot = tmp_path / "state.snapshot.json"

    built = _snapshot(root, snapshot, "--since", "2026-07-20")
    direct = _run_audit("--root", str(root), "--since", "2026-07-20", "--json", cwd=root)
    consumed = _run_audit("--snapshot-in", str(snapshot), "--since", "2026-07-20", "--json", cwd=root)

    assert _json(built) == _json(direct) == _json(consumed)
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    assert document["record_count"] == 2
    assert len(document["records"]) == len(document["record_index"]) == 2
    assert _json(consumed)["halt_count"] == 1


def test_stats_reuses_one_snapshot_for_multiple_windows(tmp_path, run_cli):
    root = tmp_path / "root"
    _write_state(root / "a", "a", "2026-07-18T00:00:00Z")
    _write_state(root / "b", "b", "2026-07-20T00:00:00Z", passes=False,
                 loop_active=False, halt_reason="halted")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0

    for since in ("2026-07-18", "2026-07-20"):
        direct = run_cli("stats", "--root", str(root), "--since", since, "--json", cwd=root)
        reused = run_cli("stats", "--snapshot", str(snapshot), "--since", since, "--json", cwd=root)
        assert _json(reused) == _json(direct)


def test_snapshot_observed_at_freezes_health_classification(tmp_path, run_cli):
    root = tmp_path / "root"
    _write_state(root, "active", "2026-07-21T11:30:00Z", passes=False,
                 loop_active=True, phase="execution", score_history=[])
    snapshot = tmp_path / "state.snapshot.json"
    built = _snapshot(root, snapshot, env={"MISSION_AUDIT_NOW": "2026-07-21T12:00:00Z",
                                           "MISSION_STALE_ACTIVE_SECONDS": "3600"})
    assert _json(built)["active_no_score_count"] == 1

    audit_reused = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root,
                              env={"MISSION_AUDIT_NOW": "2026-07-21T14:00:00Z",
                                   "MISSION_STALE_ACTIVE_SECONDS": "3600"})
    stats_reused = run_cli("stats", "--snapshot", str(snapshot), "--json", cwd=root,
                           env_extra={"MISSION_STALE_ACTIVE_SECONDS": "3600"})
    assert _json(audit_reused)["active_no_score_count"] == 1
    assert _json(stats_reused)["active_no_score_count"] == 1


@pytest.mark.parametrize("change", ["update", "delete", "add"])
def test_snapshot_rejects_live_state_inventory_changes(tmp_path, change):
    root = tmp_path / "root"
    state_path = _write_state(root, "one", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0
    if change == "update":
        state_path.write_text(state_path.read_text().replace("snapshot one", "changed one"))
    elif change == "delete":
        state_path.unlink()
    else:
        _write_state(root, "two", "2026-07-21T00:00:00Z")

    result = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root)
    assert result.returncode != 0
    assert "snapshot" in result.stderr.lower()


def test_snapshot_rejects_referenced_evidence_appearing_after_capture(tmp_path):
    root = tmp_path / "root"
    evidence = root / ".mission-state" / "scores" / "iter-1.json"
    _write_state(
        root, "one", "2026-07-21T00:00:00Z",
        score_history=[{
            "iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {},
            "scoring_evidence_path": ".mission-state/scores/iter-1.json",
        }],
    )
    snapshot = tmp_path / "state.snapshot.json"
    built = _snapshot(root, snapshot)
    assert _json(built)["missing_scoring_evidence_count"] == 1
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"items":{"accuracy":4.5}}\n', encoding="utf-8")

    result = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root)
    assert result.returncode != 0
    assert "snapshot" in result.stderr.lower()


def test_snapshot_keeps_missing_scoring_finding_gate(tmp_path):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z", score_history=[{
        "iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {},
        "scoring_evidence_path": ".mission-state/missing-score.json",
    }])
    snapshot = tmp_path / "state.snapshot.json"
    direct = _snapshot(root, snapshot)
    reused = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root)
    assert _json(direct)["missing_scoring_evidence_count"] == 1
    assert _json(reused)["missing_scoring_evidence_count"] == 1
    assert any(item["code"] == "missing-scoring-evidence" for item in _json(reused)["findings"])


def test_snapshot_supports_legacy_archive_state(tmp_path):
    root = tmp_path / "root"
    live = _write_state(root, "legacy", "2026-07-21T00:00:00Z")
    legacy = root / ".mission-state" / "archive" / "worktree-old" / "state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(live.read_bytes())
    live.unlink()
    snapshot = tmp_path / "state.snapshot.json"
    direct = _snapshot(root, snapshot)
    reused = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root)
    assert _json(direct) == _json(reused)
    assert _json(reused)["total_sessions"] == 1


def test_snapshot_rejects_root_ordered_multiset_mismatch(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    _write_state(first, "first", "2026-07-21T00:00:00Z")
    _write_state(second, "second", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    built = _run_audit("--root", str(first), "--root", str(second),
                       "--snapshot-out", str(snapshot), "--json", cwd=tmp_path)
    assert built.returncode == 0, built.stderr

    reversed_roots = _run_audit("--root", str(second), "--root", str(first),
                                "--snapshot-in", str(snapshot), "--json", cwd=tmp_path)
    duplicate_root = _run_audit("--root", str(first), "--root", str(second),
                                "--root", str(second), "--snapshot-in", str(snapshot),
                                "--json", cwd=tmp_path)
    assert reversed_roots.returncode != 0
    assert duplicate_root.returncode != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "mission-state-snapshot/999"),
        ("cli_compatibility", "mission-state-snapshot-cli/999"),
        ("record_contract", "records/999"),
        ("discovery_contract", "discovery/999"),
        ("dedupe_contract", "dedupe/999"),
        ("record_count", 999),
        ("discovery_count", 999),
    ],
)
def test_snapshot_rejects_contract_and_count_tampering(tmp_path, field, value):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0
    _rewrite_snapshot(snapshot, lambda document: document.__setitem__(field, value))
    result = _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root)
    assert result.returncode != 0


def test_snapshot_rejects_content_tamper_and_expired_ttl(tmp_path):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    document["records"][0]["state"]["mission"] = "tampered"
    snapshot.write_text(json.dumps(document), encoding="utf-8")
    snapshot.chmod(0o600)
    assert _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root).returncode != 0

    assert _snapshot(root, snapshot).returncode == 0
    _rewrite_snapshot(snapshot, lambda item: item.__setitem__("created_at", "2000-01-01T00:00:00Z"))
    assert _run_audit("--snapshot-in", str(snapshot), "--json", cwd=root).returncode != 0


@pytest.mark.parametrize("unsafe", ["permissions", "symlink"])
def test_snapshot_rejects_unsafe_snapshot_file(tmp_path, unsafe):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0
    target = snapshot
    if unsafe == "permissions":
        snapshot.chmod(0o644)
    else:
        link = tmp_path / "linked.snapshot.json"
        link.symlink_to(snapshot)
        target = link
    result = _run_audit("--snapshot-in", str(target), "--json", cwd=root)
    assert result.returncode != 0


def test_snapshot_write_is_atomic_private_and_leaves_no_temp(tmp_path):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"
    assert _snapshot(root, snapshot).returncode == 0
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(f".{snapshot.name}.*.tmp"))


def test_invalid_snapshot_never_silently_falls_back_to_live_scan(tmp_path, run_cli):
    root = tmp_path / "root"
    _write_state(root, "one", "2026-07-21T00:00:00Z")
    invalid = tmp_path / "invalid.snapshot.json"
    invalid.write_text("{}\n", encoding="utf-8")
    invalid.chmod(0o600)
    audit = _run_audit("--snapshot-in", str(invalid), "--json", cwd=root)
    stats_result = run_cli("stats", "--snapshot", str(invalid), "--json", cwd=root)
    assert audit.returncode != 0 and not audit.stdout.strip()
    assert stats_result.returncode != 0 and not stats_result.stdout.strip()


def test_snapshot_cli_help_is_backward_compatible(tmp_path, run_cli):
    audit_help = _run_audit("--help", cwd=tmp_path)
    stats_help = run_cli("stats", "--help", cwd=tmp_path)
    assert audit_help.returncode == stats_help.returncode == 0
    assert "--snapshot-out" in audit_help.stdout
    assert "--snapshot-in" in audit_help.stdout
    assert "--snapshot" in stats_help.stdout
    assert "--root" in audit_help.stdout and "--root" in stats_help.stdout


def test_snapshot_consume_skips_live_state_json_parse(tmp_path):
    root = tmp_path / "root"
    for index in range(4):
        _write_state(root / f"p{index}", f"s{index}", "2026-07-21T00:00:00Z")
    snapshot = tmp_path / "state.snapshot.json"

    spec = importlib.util.spec_from_file_location("audit_issue210_parse_count", AUDIT_PY)
    assert spec and spec.loader
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    audit.create_state_snapshot([root], snapshot, ttl_seconds=3600)

    state_parse_count = 0
    real_loads = audit.json.loads

    def counted_loads(value, *args, **kwargs):
        nonlocal state_parse_count
        parsed = real_loads(value, *args, **kwargs)
        if isinstance(parsed, dict) and {"mission", "mission_id", "session_id"} <= parsed.keys():
            state_parse_count += 1
        return parsed

    audit.json.loads = counted_loads
    records, _invalid, _roots, _observed = audit.consume_state_snapshot(snapshot, None)
    assert len(records) == 4
    assert state_parse_count == 0

