"""Issue #239: false stale 止血 — PID 不在を即 stale-halt でなく age 条件で判定する.

Contract under test:
1. find_agent_pid() が fallback した場合、stamp_metadata で pid_source="fallback" を記録
2. cleanup-stale で pid_source="fallback" + PID 消滅 → 即 orphan halt せず age 条件で判定
3. 真に放置された state (age >= threshold + 無更新) は従来どおり stale halt する
4. pid_source="agent" の state は従来どおり PID 消滅で即 orphan halt
5. pid_source 未設定 (旧 state) → 従来どおり即 orphan halt (後方互換)
"""

import json
import os
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_mission_state():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load_mission_state()


# --- 1. stamp_metadata records pid_source ---


def test_stamp_metadata_records_fallback_source():
    data = {}
    with patch.object(MS, "find_agent_pid", return_value=99999):
        with patch.object(MS, "_last_pid_was_fallback", return_value=True):
            MS.stamp_metadata(data, Path("/tmp/test"))
    assert data["pid"] == 99999
    assert data["pid_source"] == "fallback"


def test_stamp_metadata_records_agent_source():
    data = {}
    with patch.object(MS, "find_agent_pid", return_value=12345):
        with patch.object(MS, "_last_pid_was_fallback", return_value=False):
            MS.stamp_metadata(data, Path("/tmp/test"))
    assert data["pid"] == 12345
    assert data["pid_source"] == "agent"


def test_stamp_metadata_does_not_overwrite_existing_pid_source():
    data = {"pid": 11111, "pid_source": "agent"}
    MS.stamp_metadata(data, Path("/tmp/test"))
    assert data["pid"] == 11111
    assert data["pid_source"] == "agent"


# --- 2. find_agent_pid sets _LAST_PID_WAS_FALLBACK ---


def test_find_agent_pid_sets_fallback_flag_on_miss():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"stdout": "bash", "returncode": 0})()
        with patch("os.getppid", return_value=42):
            pid = MS.find_agent_pid()
    assert pid == 42
    assert MS._last_pid_was_fallback() is True


def test_find_agent_pid_clears_fallback_flag_on_hit():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"stdout": "claude", "returncode": 0})()
        with patch("os.getppid", return_value=42):
            pid = MS.find_agent_pid()
    assert MS._last_pid_was_fallback() is False


# --- 3. cleanup-stale: fallback pid + dead + recent → skip ---


def _make_state_file(tmp_path, *, pid=12345, loop_active=True, passes=False,
                     halt_reason="", updated_at=None, score_history=None,
                     pid_source="agent", mission="test mission",
                     include_pid_source=True):
    state_dir = tmp_path / ".mission-state"
    state_dir.mkdir(exist_ok=True)
    sf = state_dir / "state.json"
    data = {
        "mission": mission,
        "pid": pid,
        "loop_active": loop_active,
        "passes": passes,
        "halt_reason": halt_reason,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "project_root": str(tmp_path),
    }
    if include_pid_source:
        data["pid_source"] = pid_source
    if score_history is not None:
        data["score_history"] = score_history
    sf.write_text(json.dumps(data))
    return sf


def test_fallback_pid_dead_recent_update_not_halted(tmp_path):
    """pid_source=fallback + PID 消滅 + 最近更新 → halt しない."""
    _make_state_file(tmp_path, pid=99998, pid_source="fallback",
                     updated_at=datetime.now(timezone.utc).isoformat())
    args = type("Args", (), {"execute": True, "root": str(tmp_path)})()
    with patch.object(MS, "_pid_is_agent", return_value=False):
        with patch("builtins.print") as mock_print:
            MS.cmd_cleanup_stale(args)
            output = json.loads(mock_print.call_args[0][0])
    assert len(output["halted"]) == 0
    assert any(s.get("reason") == "fallback-pid-unobserved" for s in output["skipped"])


def test_fallback_pid_dead_old_update_halted(tmp_path):
    """pid_source=fallback + PID 消滅 + age >= threshold → stale halt."""
    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    _make_state_file(tmp_path, pid=99997, pid_source="fallback",
                     updated_at=old_time)
    args = type("Args", (), {"execute": True, "root": str(tmp_path)})()
    with patch.object(MS, "_pid_is_agent", return_value=False):
        with patch.object(MS, "_stale_active_seconds", return_value=3600):
            with patch("builtins.print") as mock_print:
                MS.cmd_cleanup_stale(args)
                output = json.loads(mock_print.call_args[0][0])
    assert len(output["halted"]) == 1
    assert output["halted"][0].get("reason") == "fallback-stale"


# --- 4. agent pid + dead → immediate orphan halt (backward compat) ---


def test_agent_pid_dead_immediate_orphan_halt(tmp_path):
    """pid_source=agent + PID 消滅 → 即 orphan halt."""
    _make_state_file(tmp_path, pid=99996, pid_source="agent",
                     updated_at=datetime.now(timezone.utc).isoformat())
    args = type("Args", (), {"execute": True, "root": str(tmp_path)})()
    with patch.object(MS, "_pid_is_agent", return_value=False):
        with patch("builtins.print") as mock_print:
            MS.cmd_cleanup_stale(args)
            output = json.loads(mock_print.call_args[0][0])
    assert len(output["halted"]) == 1
    assert output["halted"][0].get("reason") == "orphan-dead-or-reused"


# --- 5. fallback pid alive → skip ---


def test_fallback_pid_alive_agent_skipped(tmp_path):
    """pid_source=fallback + PID alive + agent CLI → skip."""
    _make_state_file(tmp_path, pid=99995, pid_source="fallback",
                     updated_at=datetime.now(timezone.utc).isoformat())
    args = type("Args", (), {"execute": True, "root": str(tmp_path)})()
    with patch.object(MS, "_pid_is_agent", return_value=True):
        with patch("builtins.print") as mock_print:
            MS.cmd_cleanup_stale(args)
            output = json.loads(mock_print.call_args[0][0])
    assert len(output["halted"]) == 0
    assert len(output["skipped"]) >= 1


# --- 6. pre-#239 state (no pid_source) → legacy immediate halt ---


def test_legacy_state_no_pid_source_immediate_halt(tmp_path):
    """pid_source フィールドなし → 従来どおり即 orphan halt."""
    _make_state_file(tmp_path, pid=99994, pid_source="agent",
                     include_pid_source=False,
                     updated_at=datetime.now(timezone.utc).isoformat())
    args = type("Args", (), {"execute": True, "root": str(tmp_path)})()
    with patch.object(MS, "_pid_is_agent", return_value=False):
        with patch("builtins.print") as mock_print:
            MS.cmd_cleanup_stale(args)
            output = json.loads(mock_print.call_args[0][0])
    assert len(output["halted"]) == 1
    assert output["halted"][0].get("reason") == "orphan-dead-or-reused"
