"""#270: runner の並列実行 — 独立 record を worker pool で実行する.

Contract under test:
1. execute_plan(parallel=1) は plan 順で逐次実行 (現行互換)
2. execute_plan(parallel=N) は全 entry を実行し全 record を返す
3. on_record コールバックは record ごとに 1 回、lock 下で直列に呼ばれる
4. stop_on_blocked: blocked record 検出後、未開始 entry を起動しない
5. parallel=2 で実際に並行実行される (相互待ち worker が完走する)
"""

import importlib.util
import threading
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    path = BENCH / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()

ENTRIES = [("t1", "goal"), ("t1", "mission"), ("t2", "goal"), ("t2", "mission")]


def test_serial_executes_in_plan_order():
    """parallel=1: plan 順で実行され record も同順."""
    seen = []

    def worker(entry):
        seen.append(entry)
        return {"entry": entry, "run_status": "completed"}

    records, stopped = MODULE.execute_plan(ENTRIES, worker, parallel=1)
    assert seen == ENTRIES
    assert [r["entry"] for r in records] == ENTRIES
    assert stopped is False


def test_parallel_executes_all_entries():
    """parallel=3: 全 entry が実行され全 record が返る."""
    records, stopped = MODULE.execute_plan(
        ENTRIES, lambda e: {"entry": e, "run_status": "completed"}, parallel=3)
    assert sorted(r["entry"] for r in records) == sorted(ENTRIES)
    assert stopped is False


def test_on_record_called_serially_per_record():
    """on_record は record ごとに 1 回、直列に呼ばれる (JSONL append の安全性)."""
    calls = []
    lock_probe = {"depth": 0, "max_depth": 0}
    probe_lock = threading.Lock()

    def on_record(entry, record):
        with probe_lock:
            lock_probe["depth"] += 1
            lock_probe["max_depth"] = max(lock_probe["max_depth"], lock_probe["depth"])
        calls.append(entry)
        with probe_lock:
            lock_probe["depth"] -= 1

    MODULE.execute_plan(
        ENTRIES, lambda e: {"entry": e, "run_status": "completed"},
        parallel=4, on_record=on_record)
    assert sorted(calls) == sorted(ENTRIES)
    assert lock_probe["max_depth"] == 1


def test_stop_on_blocked_skips_unstarted_entries():
    """blocked 検出後、未開始 entry は起動されない (parallel=1 で決定的に検証)."""
    started = []

    def worker(entry):
        started.append(entry)
        status = "blocked" if entry == ENTRIES[1] else "completed"
        return {"entry": entry, "run_status": status}

    records, stopped = MODULE.execute_plan(
        ENTRIES, worker, parallel=1, stop_on_blocked=True)
    assert stopped is True
    assert started == ENTRIES[:2]
    assert len(records) == 2


def test_parallel_actually_concurrent():
    """parallel=2: 相互に相手の開始を待つ 2 worker が完走する (直列ならデッドロック)."""
    ev1, ev2 = threading.Event(), threading.Event()

    def worker(entry):
        if entry == "a":
            ev1.set()
            assert ev2.wait(timeout=10), "並行実行されていない"
        else:
            ev2.set()
            assert ev1.wait(timeout=10), "並行実行されていない"
        return {"entry": entry, "run_status": "completed"}

    records, _ = MODULE.execute_plan(["a", "b"], worker, parallel=2)
    assert len(records) == 2
