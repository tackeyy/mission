"""#310 (F1): last_activity_at の分離 — updated_at 汚染による wall clock 膨張の停止.

実運用監査 (2026-08-01) で、resolution/batch 書き込みが updated_at を上書きし
session 壁時計が最大 500 倍膨張 (company-os #583: raw wall 394h vs active 0.78h)。

Contract under test:
1. atomic_write_json は session 形状 (mission_id + loop_active) の dict に
   last_activity_at を自動で刻む
2. administrative=True の書き込み (cleanup-stale / resolve-archive 等) は刻まない
3. session 形状でない dict (aggregate 等) には付与しない
4. duration_sec は last_activity_at を updated_at より優先する
5. age 連鎖 (state_age_since_update_sec) は heartbeat/last_progress の次に
   last_activity_at を参照する
6. 統合: cleanup-stale の terminalize は last_activity_at を変更しない
"""

import json
import importlib.util
from pathlib import Path


def _load(name: str, rel: str):
    path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load("mission_state", "bin/mission-state.py")
MC = _load("mission_common", "lib/mission_common.py")

SESSION_SHAPE = {
    "mission": "m",
    "mission_id": "abc123",
    "loop_active": True,
    "updated_at": "2026-08-01T00:00:00Z",
}


# ===== 1-3. atomic_write_json の付与規則 =====

def test_session_write_stamps_last_activity(tmp_path):
    data = dict(SESSION_SHAPE)
    MS.atomic_write_json(tmp_path / "s.json", data)
    written = json.loads((tmp_path / "s.json").read_text())
    assert "last_activity_at" in written


def test_administrative_write_does_not_stamp(tmp_path):
    data = dict(SESSION_SHAPE)
    MS.atomic_write_json(tmp_path / "s.json", data, administrative=True)
    written = json.loads((tmp_path / "s.json").read_text())
    assert "last_activity_at" not in written


def test_administrative_write_preserves_existing_value(tmp_path):
    data = dict(SESSION_SHAPE)
    data["last_activity_at"] = "2026-08-01T01:00:00Z"
    MS.atomic_write_json(tmp_path / "s.json", data, administrative=True)
    written = json.loads((tmp_path / "s.json").read_text())
    assert written["last_activity_at"] == "2026-08-01T01:00:00Z"


def test_non_session_dict_not_stamped(tmp_path):
    agg = {"active_sessions": ["a"], "updated_at": "2026-08-01T00:00:00Z"}
    MS.atomic_write_json(tmp_path / "aggregate.json", agg)
    written = json.loads((tmp_path / "aggregate.json").read_text())
    assert "last_activity_at" not in written


# ===== 4. duration_sec の優先順位 =====

def test_duration_prefers_last_activity_over_updated():
    state = {
        "started_at": "2026-08-01T00:00:00Z",
        "last_activity_at": "2026-08-01T01:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",  # 汚染 (48h 後の管理書き込み)
    }
    assert MC.duration_sec(state) == 3600.0


def test_duration_falls_back_to_updated_at():
    state = {
        "started_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T02:00:00Z",
    }
    assert MC.duration_sec(state) == 7200.0


# ===== 5. age 連鎖 =====

def test_age_chain_uses_last_activity_before_updated():
    from datetime import datetime, timezone
    state = {
        "last_activity_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T06:00:00Z",
    }
    now = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert MC.state_age_since_update_sec(state, now=now) == 3600.0
    assert MS._state_age_since_update_sec(state, now=now) == 3600.0


# ===== 6. 統合: terminalize は last_activity_at を変更しない =====

def test_terminalize_preserves_last_activity(tmp_path, monkeypatch):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    sf = sessions / "t.json"
    state = {
        "mission": "m",
        "mission_id": "abc123",
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "pid": 999999,
        "updated_at": "2026-08-01T00:00:00Z",
        "last_activity_at": "2026-08-01T00:30:00Z",
        "session_id": "t",
    }
    sf.write_text(json.dumps(state))
    monkeypatch.chdir(tmp_path)
    halted = MS._terminalize_state_file(
        sf, tmp_path, reason="orphan: test", category="stale",
        set_terminal_phase=False, expected_pid=999999, require_dead_pid=True,
    )
    assert halted is True
    after = json.loads(sf.read_text())
    assert after["halt_reason"] == "orphan: test"
    assert after["last_activity_at"] == "2026-08-01T00:30:00Z", (
        "管理系書き込みが last_activity_at を汚染してはならない")
