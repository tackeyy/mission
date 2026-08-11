"""Issue #384: mission-audit immutable snapshot boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"


def _write_state(root: Path, mission: str = "before") -> Path:
    path = root / ".mission-state" / "sessions" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mission": mission,
        "mission_id": "mission-1",
        "session_id": "session-1",
        "project_root": str(root),
        "iteration": 1,
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
        "started_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
        "score_history": [{"iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {}}],
    }) + "\n", encoding="utf-8")
    return path


def _audit(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MISSION_")}
    return subprocess.run(
        [sys.executable, str(AUDIT_PY), *args], cwd=cwd,
        text=True, capture_output=True, env=env,
    )


def _snapshot_digest(document: dict) -> str:
    import hashlib

    payload = {key: value for key, value in document.items() if key != "content_digest"}
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def test_default_audit_reports_immutable_snapshot_identity(tmp_path):
    root = tmp_path / "root"
    _write_state(root)

    result = _audit("--root", str(root), cwd=root)

    assert result.returncode == 0, result.stderr
    assert "- snapshot ID: " in result.stdout
    assert "- snapshot digest: " in result.stdout
    json_result = _audit("--root", str(root), "--json", cwd=root)
    payload = json.loads(json_result.stdout)
    assert payload["snapshot_id"] == payload["snapshot_digest"]
    assert len(payload["snapshot_digest"]) == 64
    assert (root / ".mission-state" / "audit-snapshots" / f"{payload['snapshot_id']}.json").is_file()


def test_from_snapshot_keeps_totals_and_findings_after_current_state_changes(tmp_path):
    root = tmp_path / "root"
    state = _write_state(root, "before")

    captured = _audit("--root", str(root), "--json", cwd=root)
    assert captured.returncode == 0, captured.stderr
    state.write_text(state.read_text(encoding="utf-8").replace("before", "after"), encoding="utf-8")
    snapshot_id = json.loads(captured.stdout)["snapshot_id"]
    replayed = _audit(
        "--root", str(root), "--from-snapshot", snapshot_id, "--json", cwd=root,
    )

    assert replayed.returncode == 0, replayed.stderr
    assert replayed.stdout == captured.stdout
    assert json.loads(replayed.stdout)["findings"] == json.loads(captured.stdout)["findings"]
    assert json.loads(replayed.stdout)["total_sessions"] == json.loads(captured.stdout)["total_sessions"]


def test_from_snapshot_fails_closed_for_tamper_missing_root_mismatch_and_expiry(tmp_path):
    root = tmp_path / "root"
    _write_state(root)
    snapshot = tmp_path / "snapshot.json"
    captured = _audit("--root", str(root), "--snapshot", str(snapshot), "--json", cwd=root)
    assert captured.returncode == 0, captured.stderr

    tampered = json.loads(snapshot.read_text(encoding="utf-8"))
    tampered["records"][0]["state"]["mission"] = "tampered"
    snapshot.write_text(json.dumps(tampered), encoding="utf-8")
    snapshot.chmod(0o600)
    assert _audit("--root", str(root), "--from-snapshot", str(snapshot), "--json", cwd=root).returncode == 2

    assert _audit("--root", str(root), "--from-snapshot", str(tmp_path / "missing.json"), "--json", cwd=root).returncode == 2

    mismatch = tmp_path / "mismatch.json"
    captured = _audit("--root", str(root), "--snapshot", str(mismatch), "--json", cwd=root)
    assert captured.returncode == 0, captured.stderr
    other_root = tmp_path / "other-root"
    _write_state(other_root)
    assert _audit("--root", str(other_root), "--from-snapshot", str(mismatch), "--json", cwd=other_root).returncode == 2

    expired = json.loads(mismatch.read_text(encoding="utf-8"))
    expired["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=expired["ttl_seconds"] + 1)).isoformat()
    expired["content_digest"] = _snapshot_digest(expired)
    mismatch.write_text(json.dumps(expired), encoding="utf-8")
    mismatch.chmod(0o600)
    assert _audit("--root", str(root), "--from-snapshot", str(mismatch), "--json", cwd=root).returncode == 2


def test_privacy_report_hides_scanned_root_path(tmp_path):
    root = tmp_path / "sensitive-root"
    _write_state(root)

    result = _audit("--root", str(root), "--privacy", cwd=root)

    assert result.returncode == 0, result.stderr
    assert str(root) not in result.stdout
    assert "root-1" in result.stdout
    json_result = _audit("--root", str(root), "--privacy", "--json", cwd=root)
    assert str(root) not in json_result.stdout
    assert json.loads(json_result.stdout)["snapshot_id"]
