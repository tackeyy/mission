"""Issue #389: stop-hook output is deduplicated by unfinished-state changes."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


HOOK = Path(__file__).resolve().parents[3] / "scripts" / "mission-stop-guard.sh"


def _write_session(root: Path, session_id: str, **overrides: object) -> Path:
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "session_id": session_id,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": "executing",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "mission": "dedupe fixture",
        "project_root": str(root),
        "pid": os.getpid(),
    }
    payload.update(overrides)
    path = sessions / f"{session_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_hook(root: Path, *, now_epoch: int = 1_800_000_000) -> dict[str, object]:
    env = {
        "PATH": os.environ["PATH"],
        "CODEX_THREAD_ID": "dedupe",
        "MISSION_STOP_GUARD_NOW_EPOCH": str(now_epoch),
        "MISSION_STOP_GUARD_HEARTBEAT_SECONDS": "600",
    }
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"stop_hook_active": False, "cwd": str(root)}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout)


def test_unchanged_unfinished_set_emits_compact_heartbeat_after_first_detail(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")

    first = _run_hook(tmp_path)
    second = _run_hook(tmp_path, now_epoch=1_800_000_001)

    assert "未達一覧" in str(first["reason"])
    heartbeat = str(second["reason"])
    assert "未達一覧" not in heartbeat
    assert "heartbeat" in heartbeat
    assert "blocker=unfinished-mission" in heartbeat
    assert "next=python3 scripts/mission-state.py next" in heartbeat
    assert "dedupe fixture" not in heartbeat
    assert "last_score" not in heartbeat
    assert "blocks=" not in heartbeat
    assert "reinjections=" not in heartbeat


def test_phase_and_lease_changes_restore_full_detail(tmp_path: Path) -> None:
    session = _write_session(tmp_path, "cx-dedupe", issue_ref="389")
    _run_hook(tmp_path)
    assert "heartbeat" in str(_run_hook(tmp_path, now_epoch=1_800_000_001)["reason"])

    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["phase"] = "reviewing"
    session.write_text(json.dumps(payload), encoding="utf-8")
    phase_changed = _run_hook(tmp_path, now_epoch=1_800_000_002)

    payload["lease_expires_at"] = "2027-01-15T08:00:00Z"
    session.write_text(json.dumps(payload), encoding="utf-8")
    lease_changed = _run_hook(tmp_path, now_epoch=1_800_000_003)

    assert "未達一覧" in str(phase_changed["reason"])
    assert "未達一覧" in str(lease_changed["reason"])


def test_unchanged_set_refreshes_full_detail_after_heartbeat_ttl(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")

    first = _run_hook(tmp_path)
    before_ttl = _run_hook(tmp_path, now_epoch=1_800_000_599)
    at_ttl = _run_hook(tmp_path, now_epoch=1_800_000_600)

    assert "未達一覧" in str(first["reason"])
    assert "heartbeat" in str(before_ttl["reason"])
    assert "未達一覧" in str(at_ttl["reason"])


def test_block_and_reinjection_counters_persist_without_mutating_session_state(tmp_path: Path) -> None:
    session = _write_session(tmp_path, "cx-dedupe", issue_ref="389")
    original = session.read_bytes()

    _run_hook(tmp_path)
    _run_hook(tmp_path, now_epoch=1_800_000_001)
    payload = json.loads(session.read_text(encoding="utf-8"))
    payload["phase"] = "reviewing"
    session.write_text(json.dumps(payload), encoding="utf-8")
    changed_session = session.read_bytes()
    _run_hook(tmp_path, now_epoch=1_800_000_002)

    sidecars = list((tmp_path / ".mission-state" / "sessions").glob(".*.stop-guard"))
    assert len(sidecars) == 1
    counters = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert counters == {
        "schema": "mission-stop-guard/1",
        "session_id": "cx-dedupe",
        "last_digest": counters["last_digest"],
        "last_detail_epoch": 1_800_000_002,
        "block_count": 3,
        "reinjection_count": 3,
        "detail_count": 2,
        "heartbeat_count": 1,
    }
    assert session.read_bytes() == changed_session
    assert original != changed_session


def test_child_completion_and_new_init_restore_full_detail(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389", logical_group_id="group-389")
    other = _write_session(
        tmp_path, "cx-other", issue_ref="390", logical_group_id="group-389"
    )
    _run_hook(tmp_path)
    assert "heartbeat" in str(_run_hook(tmp_path, now_epoch=1_800_000_001)["reason"])

    completed = json.loads(other.read_text(encoding="utf-8"))
    completed.update({"loop_active": False, "passes": True})
    other.write_text(json.dumps(completed), encoding="utf-8")
    after_completion = _run_hook(tmp_path, now_epoch=1_800_000_002)

    _write_session(tmp_path, "cx-late", issue_ref="391", logical_group_id="group-389")
    after_new_init = _run_hook(tmp_path, now_epoch=1_800_000_003)

    assert "未達一覧" in str(after_completion["reason"])
    assert "未達一覧" in str(after_new_init["reason"])


def test_legacy_session_filename_change_restores_full_detail(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")
    legacy = _write_session(tmp_path, "legacy-a", issue_ref="390")
    legacy_payload = json.loads(legacy.read_text(encoding="utf-8"))
    legacy_payload.pop("session_id")
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

    _run_hook(tmp_path)
    assert "heartbeat" in str(_run_hook(tmp_path, now_epoch=1_800_000_001)["reason"])

    legacy.rename(legacy.with_name("legacy-b.json"))
    after_filename_change = _run_hook(tmp_path, now_epoch=1_800_000_002)

    assert "未達一覧" in str(after_filename_change["reason"])


def test_duplicate_counter_keys_fail_safe_without_rewriting_sidecar(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")
    _run_hook(tmp_path)
    token = hashlib.sha256(b"cx-dedupe").hexdigest()[:16]
    sidecar = tmp_path / ".mission-state" / "sessions" / f".{token}.stop-guard"
    current = json.loads(sidecar.read_text(encoding="utf-8"))["last_digest"]
    duplicate = sidecar.read_text(encoding="utf-8").replace(
        f'"last_digest":"{current}"',
        f'"last_digest":"{"0" * 64}","last_digest":"{current}"',
    )
    sidecar.write_text(duplicate, encoding="utf-8")
    before = sidecar.read_bytes()

    payload = _run_hook(tmp_path, now_epoch=1_800_000_001)

    assert "未達一覧" in str(payload["reason"])
    assert sidecar.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "malformed", "oversize"])
def test_unsafe_counter_sidecar_fails_safe_without_external_write(
    tmp_path: Path, kind: str,
) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")
    external = tmp_path / "external.json"
    external.write_text("external-content", encoding="utf-8")
    token = hashlib.sha256(b"cx-dedupe").hexdigest()[:16]
    sidecar = tmp_path / ".mission-state" / "sessions" / f".{token}.stop-guard"
    if kind == "symlink":
        sidecar.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, sidecar)
    elif kind == "malformed":
        sidecar.write_text("{", encoding="utf-8")
    else:
        sidecar.write_bytes(b"x" * (64 * 1024 + 1))
    before = sidecar.read_bytes()

    payload = _run_hook(tmp_path)

    assert payload["decision"] == "block"
    assert "未達一覧" in str(payload["reason"])
    assert sidecar.read_bytes() == before
    assert external.read_text(encoding="utf-8") == "external-content"


def test_symlinked_state_ancestor_does_not_receive_counter_writes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    external_state = tmp_path / "external-state"
    sessions = external_state / "sessions"
    sessions.mkdir(parents=True)
    payload = {
        "session_id": "cx-dedupe",
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": "executing",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "mission": "external ancestor fixture",
        "project_root": str(root),
        "pid": os.getpid(),
    }
    (sessions / "cx-dedupe.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / ".mission-state").symlink_to(external_state, target_is_directory=True)

    result = _run_hook(root)

    assert result["decision"] == "block"
    assert "未達一覧" in str(result["reason"])
    assert list(sessions.glob(".*.stop-guard")) == []


def test_concurrent_blocks_keep_all_counter_updates(tmp_path: Path) -> None:
    _write_session(tmp_path, "cx-dedupe", issue_ref="389")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _run_hook(tmp_path), range(8)))

    token = hashlib.sha256(b"cx-dedupe").hexdigest()[:16]
    sidecar = tmp_path / ".mission-state" / "sessions" / f".{token}.stop-guard"
    counters = json.loads(sidecar.read_text(encoding="utf-8"))
    assert counters["block_count"] == 8
    assert counters["reinjection_count"] == 8
    assert counters["detail_count"] == 1
    assert counters["heartbeat_count"] == 7
    assert sum("未達一覧" in str(result["reason"]) for result in results) == 1
