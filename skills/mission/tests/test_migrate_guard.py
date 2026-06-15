"""P4: mission-migrate.py の loop_active ガード (進行中 state の migrate 中断防止)."""
import importlib.util
import json
from pathlib import Path

MIGRATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-migrate.py"


def _load():
    spec = importlib.util.spec_from_file_location("gm", MIGRATE_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _state(tmp_path, **kw):
    sd = tmp_path / ".mission-state"; sd.mkdir(exist_ok=True)
    base = {"loop_active": False, "passes": False, "halt_reason": "", "session_id": "s", "mission_id": "g"}
    base.update(kw)
    (sd / "state.json").write_text(json.dumps(base))
    return sd


def test_migrate_blocks_loop_active(tmp_path):
    m = _load()
    sd = _state(tmp_path, loop_active=True)
    r = m.migrate_one(sd / "state.json", execute=True, remove_legacy=False)
    assert r["status"] == "skipped" and "loop_active" in r["reason"]
    assert not (sd / "sessions").exists()


def test_migrate_force_overrides(tmp_path):
    m = _load()
    sd = _state(tmp_path, loop_active=True)
    r = m.migrate_one(sd / "state.json", execute=True, remove_legacy=False, force=True)
    assert r["status"] == "migrated"
    assert (sd / "sessions" / "s.json").exists()


def test_migrate_completed_state_ok(tmp_path):
    """passes=true (完了済) は進行中でないので migrate 可能."""
    m = _load()
    sd = _state(tmp_path, loop_active=False, passes=True)
    r = m.migrate_one(sd / "state.json", execute=True, remove_legacy=False)
    assert r["status"] == "migrated"


def test_migrate_backfills_pid(tmp_path):
    """pid 未設定 legacy は migrate で null 補完される (hook owner check 用)."""
    m = _load()
    sd = tmp_path / ".mission-state"; sd.mkdir()
    (sd / "state.json").write_text(json.dumps({"loop_active": False, "passes": True, "session_id": "s"}))
    m.migrate_one(sd / "state.json", execute=True, remove_legacy=False)
    d = json.loads((sd / "sessions" / "s.json").read_text())
    assert "pid" in d and d["pid"] is None
