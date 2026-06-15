"""P2: multi-session の list/cleanup/aggregate ライフサイクル (並列実行の状態管理)."""
import importlib.util
import json
from pathlib import Path

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("gs_life", MISSION_STATE_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_iter_state_files_includes_sessions(tmp_path):
    """_iter_state_files が legacy state.json と sessions/*.json の両方を列挙."""
    m = _load()
    sd = tmp_path / ".mission-state"; (sd / "sessions").mkdir(parents=True)
    (sd / "state.json").write_text("{}")
    (sd / "sessions" / "A.json").write_text("{}")
    (sd / "sessions" / "B.json").write_text("{}")
    found = {p.name for p in m._iter_state_files(tmp_path)}
    assert found == {"state.json", "A.json", "B.json"}


def test_project_root_of_handles_sessions(tmp_path):
    """_project_root_of が sessions/<sid>.json でも正しい proj を返す."""
    m = _load()
    legacy = tmp_path / ".mission-state" / "state.json"
    sess = tmp_path / ".mission-state" / "sessions" / "x.json"
    assert m._project_root_of(legacy) == tmp_path
    assert m._project_root_of(sess) == tmp_path  # sf.parent.parent では .mission-state になる罠を回避


def test_cleanup_stale_halts_dead_session(tmp_path, run_cli):
    """multi の sessions/<sid>.json で pid が dead なら cleanup-stale が halt する."""
    env = {"MISSION_SESSION_ID": "dead-sess"}
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra=env, check=True)
    sf = tmp_path / ".mission-state" / "sessions" / "dead-sess.json"
    d = json.loads(sf.read_text()); d["pid"] = 999999; sf.write_text(json.dumps(d))
    r = run_cli("cleanup-stale", "--root", str(tmp_path), "--execute", cwd=tmp_path)
    assert r.returncode == 0
    d2 = json.loads(sf.read_text())
    assert d2["loop_active"] is False and "orphan" in d2["halt_reason"]


def test_aggregate_removes_on_halt(tmp_path, run_cli):
    """2セッション init 後、1つを mark-halt すると aggregate の active_sessions から除去."""
    a = {"MISSION_SESSION_ID": "A"}
    b = {"MISSION_SESSION_ID": "B"}
    run_cli("init", "ga", "--complexity", "Standard", cwd=tmp_path, env_extra=a, check=True)
    run_cli("init", "gb", "--complexity", "Standard", cwd=tmp_path, env_extra=b, check=True)
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert set(agg["active_sessions"]) == {"A", "B"}
    run_cli("mark-halt", "--reason", "done", cwd=tmp_path, env_extra=a, check=True)
    agg2 = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert agg2["active_sessions"] == ["B"]


def test_concurrent_init_all_in_aggregate(tmp_path, run_cli):
    """3セッション同時 init (threading) で StateLock により全 sid が aggregate に登録される."""
    import threading
    errors = []

    def _init(sid):
        r = run_cli("init", f"mission-{sid}", "--complexity", "Standard",
                    cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid})
        if r.returncode != 0:
            errors.append(r.stderr)

    ts = [threading.Thread(target=_init, args=(s,)) for s in ("X", "Y", "Z")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert set(agg["active_sessions"]) == {"X", "Y", "Z"}
