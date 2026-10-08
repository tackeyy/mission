"""P4: mission-migrate.py の loop_active ガード (進行中 state の migrate 中断防止)."""
import importlib.util
import json
import pytest
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


def test_migration_cli_rejects_unencodable_state_before_publication(tmp_path):
    import subprocess
    import sys

    sd = _state(tmp_path, passes=True, custom_note='\ud800')
    before = {p.relative_to(sd): p.read_bytes() for p in sd.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, str(MIGRATE_PY), str(tmp_path), '--execute'],
                            capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'canonical-json-invalid' in result.stdout + result.stderr
    assert {p.relative_to(sd): p.read_bytes() for p in sd.rglob('*') if p.is_file()} == before


def test_migration_preserves_history_and_continues_after_capacity_refusal(tmp_path):
    import subprocess
    import sys
    from mission_kernel.state_capacity import STATE_LIMIT

    roots = [tmp_path / name for name in ('full', 'historical', 'ordinary')]
    for root in roots:
        root.mkdir()
    full = _state(roots[0], padding='p' * (STATE_LIMIT - 2000))
    historical = _state(roots[1], halt_reason='h' * 3000, goal_dispatch_source='g' * 300,
        owner_session_id='old owner', lease_id='old token', fencing_epoch=1,
        lease_expires_at='2099-01-01T00:00:00Z')
    ordinary = _state(roots[2], passes=True)
    before = (full / 'state.json').read_bytes()
    result = subprocess.run([sys.executable, str(MIGRATE_PY), *(str(r) for r in roots), '--execute'],
                            capture_output=True, text=True)
    assert result.returncode == 2 and 'Traceback' not in result.stderr, result.stdout + result.stderr
    outcomes = json.loads(result.stdout)['results']
    assert [r['status'] for r in outcomes] == ['error', 'migrated', 'migrated']
    assert 'state-capacity-' in outcomes[0]['reason']
    assert not (full / 'sessions').exists() and not (full / 'state.json.pre-migration').exists()
    assert (full / 'state.json').read_bytes() == before
    copied = json.loads((historical / 'sessions/s.json').read_bytes())
    source = json.loads((historical / 'state.json').read_bytes())
    assert all(copied[key] == value for key, value in source.items())
    assert (ordinary / 'sessions/s.json').exists()


@pytest.mark.parametrize('preexisting', [False, True])
def test_migration_write_failure_removes_only_its_empty_directory(tmp_path, monkeypatch, preexisting):
    m = _load()
    sd = _state(tmp_path, passes=True)
    if preexisting:
        (sd / 'sessions').mkdir()
    def fail(*args, **kwargs):
        raise OSError('publication refused')
    monkeypatch.setattr(m._gs, '_atomic_write', fail)
    with pytest.raises(OSError, match='publication refused'):
        m.migrate_one(sd / 'state.json', execute=True, remove_legacy=False)
    assert (sd / 'sessions').exists() == preexisting
