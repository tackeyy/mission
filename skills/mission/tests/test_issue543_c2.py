"""Issue #543 C2 command ownership and real-process v5 compatibility."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

LOCK_READY_BOOTSTRAP = """
import importlib.util
import os
from pathlib import Path
import sys

script = Path(sys.argv[1])
arguments = sys.argv[2:]
spec = importlib.util.spec_from_file_location("mission_state_lock_ready", script)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original_lock = module.StateLock
ready_path = Path(os.environ["ISSUE543_LOCK_READY"])

class SignalingStateLock(original_lock):
    def __enter__(self):
        ready_path.write_text("ready", encoding="utf-8")
        return super().__enter__()

module.StateLock = SignalingStateLock
sys.argv = [str(script), *arguments]
module.main()
"""


def _load_mission_state_module(name: str):
    spec = importlib.util.spec_from_file_location(name, MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env(
    session_id: str,
    lease_id: str,
    *,
    ttl: int | None = None,
    now: str | None = None,
):
    values = {
        "MISSION_SESSION_ID": session_id,
        "MISSION_LEASE_ID": lease_id,
    }
    if ttl is not None:
        values["MISSION_LEASE_TTL_SECONDS"] = str(ttl)
    if now is not None:
        values["MISSION_STATE_NOW"] = now
    return values


def _clock():
    """リース期限切れを壁時計ではなく論理時刻で再現するための時刻列を返す (#579).

    以前は ttl=1 秒 + time.sleep(1.1) で期限切れを作っていたが、CPU 競合下では
    操作を実行するセッション自身のリースが操作中に切れ、fenced commit が
    "pending target lease expired" で落ちる確率的失敗を起こしていた。
    MISSION_STATE_NOW で now を進めれば、リースは既定 TTL のまま論理的にだけ
    期限切れになり、実行時間に依存しなくなる。
    """
    base = datetime.now(timezone.utc).replace(microsecond=0)

    def at(minutes: int) -> str:
        moment = base + timedelta(minutes=minutes)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    return at


def _head(root, session_id: str):
    path = root / ".mission-state" / "sessions" / f"{session_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _state(run_cli, root, session_id: str, lease_id: str):
    result = run_cli(
        "get",
        cwd=root,
        env_extra=_env(session_id, lease_id),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _start_lock_ready_cli(arguments, *, cwd, environment, ready_path):
    child_environment = {**environment, "ISSUE543_LOCK_READY": str(ready_path)}
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            LOCK_READY_BOOTSTRAP,
            str(MISSION_STATE_PY),
            *arguments,
        ],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_environment,
    )


def _wait_for_lock_attempt(process, ready_path):
    deadline = time.monotonic() + 10
    while not ready_path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "concurrent command exited before attempting the lock: "
                + stderr
                + stdout
            )
        if time.monotonic() >= deadline:
            raise AssertionError("concurrent command did not attempt the lock")
        time.sleep(0.01)


def test_planning_reselect_real_process_preserves_v5_and_replays(run_cli, tmp_path):
    environment = {
        **_env("planning", "planning-lease"),
        "MISSION_OPERATION_ID": "planning-reselect-attempt-1",
    }
    initialized = run_cli(
        "init",
        "legacy planning migration",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=environment,
    )
    assert initialized.returncode == 0, initialized.stderr
    assert _head(tmp_path, "planning")["schema"] == "mission-head/1"

    first = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra=environment,
    )

    assert first.returncode == 0, first.stderr
    after_first = _head(tmp_path, "planning")
    state = _state(run_cli, tmp_path, "planning", "planning-lease")
    assert after_first["schema"] == "mission-head/1"
    assert after_first["generation"] == 2
    assert state["planning_policy_version"] == 1
    assert state["specialists_candidates"] == []
    assert state["specialists_selected"] == []
    assert not (tmp_path / ".mission-state" / "sessions" / "planning.json.bak").exists()

    replay = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra=environment,
    )

    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == json.loads(first.stdout)
    assert _head(tmp_path, "planning") == after_first


def test_planning_reselect_distinguishes_a_new_invocation_from_a_retry(
    run_cli, tmp_path
):
    environment = _env("planning", "planning-lease")
    assert run_cli(
        "init",
        "planning operation identity",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=environment,
    ).returncode == 0
    first = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra={**environment, "MISSION_OPERATION_ID": "reselect-first"},
    )
    assert first.returncode == 0, first.stderr
    reset = run_cli(
        "set",
        "planning_policy_version=0",
        cwd=tmp_path,
        env_extra=environment,
    )
    assert reset.returncode == 0, reset.stderr
    before_second = _head(tmp_path, "planning")

    second = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra={**environment, "MISSION_OPERATION_ID": "reselect-second"},
    )

    assert second.returncode == 0, second.stderr
    assert _state(run_cli, tmp_path, "planning", "planning-lease")[
        "planning_policy_version"
    ] == 1
    assert _head(tmp_path, "planning")["generation"] == before_second["generation"] + 1


def test_planning_reselect_v5_rejects_a_stale_fencing_token(run_cli, tmp_path):
    owner = _env("planning", "owner-lease")
    assert run_cli(
        "init",
        "planning fencing",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=owner,
    ).returncode == 0
    before = _head(tmp_path, "planning")

    rejected = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra={
            **_env("planning", "wrong-lease"),
            "MISSION_OPERATION_ID": "planning-stale-token-attempt",
        },
    )

    assert rejected.returncode == 2
    assert "lease" in rejected.stderr
    assert _head(tmp_path, "planning") == before


def test_planning_reselect_v5_requires_a_caller_stable_operation_id(
    run_cli, tmp_path
):
    environment = _env("planning", "planning-lease")
    assert run_cli(
        "init",
        "planning operation contract",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=environment,
    ).returncode == 0
    before = _head(tmp_path, "planning")

    rejected = run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra=environment,
    )

    assert rejected.returncode == 2
    assert "MISSION_OPERATION_ID" in rejected.stderr
    assert _head(tmp_path, "planning") == before


def test_planning_adopt_core_fails_closed_when_not_in_planning_phase(
    run_cli, tmp_path
):
    # Keep this on a v5 session: the point of the observation is that the domain
    # gate still fails closed after the Stage B migration, and a retained-v4
    # fallback would stop covering the v5 path entirely.
    environment = _env("stage-b-observation", "stage-b-observation-lease")
    initialized = run_cli(
        "init",
        "observe retained Stage B command",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=environment,
    )
    assert initialized.returncode == 0, initialized.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / "stage-b-observation.json"
    # Disable the planning policy through the CLI so adopt-core hits the phase
    # gate; under v5 the session file is a head record and cannot be edited here.
    disabled = run_cli(
        "set",
        "planning_policy_version=0",
        cwd=tmp_path,
        env_extra={**environment, "MISSION_OPERATION_ID": "stage-b-observation-disable"},
    )
    assert disabled.returncode == 0, disabled.stderr
    before = state_path.read_bytes()
    plan = tmp_path / "stage-b-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema": "mission-plan/1",
                "objective": "observe one retained Stage B command",
                "scope": {
                    "resources": [],
                    "actions": [
                        {"type": "analyze", "effect_class": "reversible"}
                    ],
                },
                "assumptions": [
                    {
                        "id": "input",
                        "statement": "the fixture exists",
                        "validation": "read the fixture",
                    }
                ],
                "steps": [
                    {
                        "id": "observe",
                        "action": "analyze",
                        "inputs": [],
                        "outputs": ["finding"],
                        "depends_on": [],
                        "acceptance_checks": ["finding is recorded"],
                        "risk": "low",
                        "rollback": "none",
                    }
                ],
                "global_acceptance": ["observation is complete"],
                "stop_conditions": ["fixture is unavailable"],
            }
        ),
        encoding="utf-8",
    )

    observed = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        cwd=tmp_path,
        env_extra={**environment, "MISSION_OPERATION_ID": "stage-b-observation-adopt"},
    )

    assert observed.returncode != 0
    assert "planning-policy-not-active" in observed.stderr
    assert state_path.read_bytes() == before


def test_planning_reselect_preserves_retained_v4_behavior(
    legacy_run_cli, tmp_path
):
    environment = _env("planning-v4", "planning-v4-lease")
    initialized = legacy_run_cli(
        "init",
        "retained v4 planning migration",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=environment,
    )
    assert initialized.returncode == 0, initialized.stderr
    before = _head(tmp_path, "planning-v4")
    assert "schema" not in before

    result = legacy_run_cli(
        "planning",
        "reselect",
        cwd=tmp_path,
        env_extra=environment,
    )

    assert result.returncode == 0, result.stderr
    state = _head(tmp_path, "planning-v4")
    assert "schema" not in state
    assert state["planning_policy_version"] == 1
    assert state["specialists_candidates"] == []
    assert state["specialists_selected"] == []


def test_supersede_reviews_real_process_preserves_all_v5_heads_and_replays(
    run_cli, tmp_path
):
    common = [
        "init",
        "review generation",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "c2-review-group",
    ]
    old_environment = _env("old-review", "old-review-lease", ttl=1)
    old = run_cli(*common, cwd=tmp_path, env_extra=old_environment)
    assert old.returncode == 0, old.stderr
    time.sleep(1.1)
    current_environment = _env("current-review", "current-review-lease")
    current = run_cli(*common, cwd=tmp_path, env_extra=current_environment)
    assert current.returncode == 0, current.stderr

    operation_environment = {
        **current_environment,
        "MISSION_OPERATION_ID": "supersede-c2-review-group-1",
    }
    first = run_cli(
        "supersede-reviews",
        "--group",
        "c2-review-group",
        cwd=tmp_path,
        env_extra=operation_environment,
    )

    assert first.returncode == 0, first.stderr
    old_head = _head(tmp_path, "old-review")
    current_head = _head(tmp_path, "current-review")
    old_state = _state(run_cli, tmp_path, "old-review", "old-review-lease")
    current_state = _state(
        run_cli, tmp_path, "current-review", "current-review-lease"
    )
    assert old_head["schema"] == current_head["schema"] == "mission-head/1"
    assert old_head["generation"] == current_head["generation"] == 2
    assert old_state["terminal_outcome"] == "stale_superseded"
    assert old_state["passes"] is False and old_state["loop_active"] is False
    assert current_state["loop_active"] is True
    assert current_state["supersedes"] == ["old-review"]
    assert not list((tmp_path / ".mission-state" / "sessions").glob("*.bak"))

    replay = run_cli(
        "supersede-reviews",
        "--group",
        "c2-review-group",
        cwd=tmp_path,
        env_extra=operation_environment,
    )

    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == json.loads(first.stdout)
    assert _head(tmp_path, "old-review") == old_head
    assert _head(tmp_path, "current-review") == current_head


def test_supersede_reviews_retained_v4_rolls_back_all_members_on_write_failure(
    legacy_run_cli, monkeypatch, tmp_path
):
    common = [
        "init",
        "retained v4 review generation",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "v4-rollback-group",
    ]
    environments = {
        session_id: _env(session_id, session_id + "-lease")
        for session_id in ("v4-old-1", "v4-old-2", "v4-current")
    }
    for session_id in ("v4-old-1", "v4-old-2", "v4-current"):
        result = legacy_run_cli(
            *common,
            cwd=tmp_path,
            env_extra=environments[session_id],
        )
        assert result.returncode == 0, result.stderr
    session_paths = sorted((tmp_path / ".mission-state" / "sessions").glob("*.json"))
    before = {path: path.read_bytes() for path in session_paths}
    module = _load_mission_state_module("mission_state_issue543_v4_rollback")
    original_write = module.atomic_write_json
    writes = 0

    def fail_second_session_write(path, data, **kwargs):
        nonlocal writes
        if Path(path).parent.name == "sessions":
            writes += 1
            if writes == 2:
                raise OSError("injected second review write failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_second_session_write)
    monkeypatch.chdir(tmp_path)
    for key, value in environments["v4-current"].items():
        monkeypatch.setenv(key, value)

    try:
        module.cmd_supersede_reviews(SimpleNamespace(group="v4-rollback-group"))
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("injected write failure was not propagated")

    assert {
        path: json.loads(path.read_text(encoding="utf-8")) for path in session_paths
    } == {path: json.loads(payload) for path, payload in before.items()}


def test_supersede_reviews_mixed_v5_v4_group_recovers_on_one_same_id_retry(
    run_cli, monkeypatch, tmp_path
):
    from .mission_state_fixture_corpus import _materialize_legacy_init_fixture

    common = [
        "init",
        "mixed repository review generation",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "mixed-retry-group",
    ]
    first_old_environment = _env("mixed-old-v5-1", "mixed-old-v5-1-lease", ttl=1)
    second_old_environment = _env("mixed-old-v5-2", "mixed-old-v5-2-lease", ttl=1)
    current_environment = _env("mixed-current-v4", "mixed-current-v4-lease")
    assert run_cli(
        *common, cwd=tmp_path, env_extra=first_old_environment
    ).returncode == 0
    assert run_cli(
        *common, cwd=tmp_path, env_extra=second_old_environment
    ).returncode == 0
    assert run_cli(
        *common, cwd=tmp_path, env_extra=current_environment
    ).returncode == 0
    _materialize_legacy_init_fixture(
        tmp_path,
        session_ids=("mixed-current-v4",),
        cleanup_v5=False,
    )
    time.sleep(1.1)
    module = _load_mission_state_module("mission_state_issue543_mixed_retry")
    original_write = module.atomic_write_json
    current_path = (
        tmp_path / ".mission-state" / "sessions" / "mixed-current-v4.json"
    )
    injected = False

    def fail_current_v4_write(path, data, **kwargs):
        nonlocal injected
        if Path(path) == current_path and not injected:
            injected = True
            raise OSError("injected mixed-group v4 write failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_current_v4_write)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        **current_environment,
        "MISSION_OPERATION_ID": "supersede-mixed-retry",
    }.items():
        monkeypatch.setenv(key, value)

    try:
        module.cmd_supersede_reviews(SimpleNamespace(group="mixed-retry-group"))
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("injected mixed-group failure was not propagated")
    assert injected
    old_after_failure = {
        session_id: _state(run_cli, tmp_path, session_id, session_id + "-lease")
        for session_id in ("mixed-old-v5-1", "mixed-old-v5-2")
    }
    current_after_failure = _head(tmp_path, "mixed-current-v4")
    assert all(
        state["terminal_outcome"] == "stale_superseded"
        for state in old_after_failure.values()
    )
    assert current_after_failure.get("supersedes") == []

    monkeypatch.setattr(module, "atomic_write_json", original_write)
    module.cmd_supersede_reviews(SimpleNamespace(group="mixed-retry-group"))

    old_after_retry = {
        session_id: _state(run_cli, tmp_path, session_id, session_id + "-lease")
        for session_id in ("mixed-old-v5-1", "mixed-old-v5-2")
    }
    current_after_retry = _head(tmp_path, "mixed-current-v4")
    assert all(
        state["terminal_outcome"] == "stale_superseded"
        for state in old_after_retry.values()
    )
    assert current_after_retry["supersedes"] == [
        "mixed-old-v5-1",
        "mixed-old-v5-2",
    ]


def test_supersede_reviews_v5_requires_a_caller_stable_operation_id(
    run_cli, tmp_path
):
    common = [
        "init",
        "supersede operation contract",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "supersede-operation-group",
    ]
    old_environment = _env("operation-old", "operation-old-lease", ttl=1)
    current_environment = _env("operation-current", "operation-current-lease")
    assert run_cli(*common, cwd=tmp_path, env_extra=old_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=current_environment).returncode == 0
    time.sleep(1.1)
    before = {
        session_id: _head(tmp_path, session_id)
        for session_id in ("operation-old", "operation-current")
    }

    rejected = run_cli(
        "supersede-reviews",
        "--group",
        "supersede-operation-group",
        cwd=tmp_path,
        env_extra=current_environment,
    )

    assert rejected.returncode == 2
    assert "MISSION_OPERATION_ID" in rejected.stderr
    assert {
        session_id: _head(tmp_path, session_id)
        for session_id in ("operation-old", "operation-current")
    } == before


def test_supersede_reviews_retry_recovers_a_terminal_old_v5_prepare(
    run_cli, monkeypatch, tmp_path
):
    common = [
        "init",
        "crash recovery review generation",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "crash-recovery-group",
    ]
    old_environment = _env("crash-old", "crash-old-lease", ttl=1)
    current_environment = _env("crash-current", "crash-current-lease")
    assert run_cli(*common, cwd=tmp_path, env_extra=old_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=current_environment).returncode == 0
    time.sleep(1.1)
    module = _load_mission_state_module("mission_state_issue543_crash_retry")
    original_factory = module._legacy_lifecycle_repository
    injected = False

    def faulting_factory(*args, **kwargs):
        nonlocal injected
        repository = original_factory(*args, **kwargs)
        if not injected and isinstance(repository, module.V5CompatibilityRepository):
            def fail_after_head(point):
                nonlocal injected
                if point == "after-head-replace" and not injected:
                    injected = True
                    raise OSError("injected crash after old head publish")

            repository._repository.fault_injector = fail_after_head
        return repository

    monkeypatch.setattr(module, "_legacy_lifecycle_repository", faulting_factory)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        **current_environment,
        "MISSION_OPERATION_ID": "supersede-crash-recovery",
    }.items():
        monkeypatch.setenv(key, value)

    try:
        module.cmd_supersede_reviews(
            SimpleNamespace(group="crash-recovery-group")
        )
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("fault injection did not interrupt supersede")
    assert injected

    module.cmd_supersede_reviews(SimpleNamespace(group="crash-recovery-group"))

    old_state = _state(run_cli, tmp_path, "crash-old", "crash-old-lease")
    current_state = _state(
        run_cli,
        tmp_path,
        "crash-current",
        "crash-current-lease",
    )
    assert old_state["terminal_outcome"] == "stale_superseded"
    assert current_state["supersedes"] == ["crash-old"]
    assert not list(
        (tmp_path / ".mission-state" / "transactions" / "prepared").glob("*.json")
    )


def test_supersede_reviews_skips_already_terminal_v5_generations(
    run_cli, tmp_path
):
    common = [
        "init",
        "successive review wave",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "successive-wave-group",
    ]
    at = _clock()
    first_environment = _env("wave-1", "wave-1-lease", now=at(0))
    second_environment = _env("wave-2", "wave-2-lease", now=at(0))
    third_environment = _env("wave-3", "wave-3-lease", now=at(20))
    assert run_cli(*common, cwd=tmp_path, env_extra=first_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=second_environment).returncode == 0
    # 既定 TTL は 15 分。20 分進めて wave-1 のリースを論理的に期限切れにする。
    first_supersede = run_cli(
        "supersede-reviews",
        "--group",
        "successive-wave-group",
        cwd=tmp_path,
        env_extra={
            **second_environment,
            "MISSION_STATE_NOW": at(20),
            "MISSION_OPERATION_ID": "supersede-successive-wave-1",
        },
    )
    assert first_supersede.returncode == 0, first_supersede.stderr
    assert run_cli(*common, cwd=tmp_path, env_extra=third_environment).returncode == 0

    # さらに 20 分進めて wave-2 のリースを期限切れにする。
    second_supersede = run_cli(
        "supersede-reviews",
        "--group",
        "successive-wave-group",
        cwd=tmp_path,
        env_extra={
            **third_environment,
            "MISSION_STATE_NOW": at(40),
            "MISSION_OPERATION_ID": "supersede-successive-wave-2",
        },
    )

    assert second_supersede.returncode == 0, second_supersede.stderr
    states = {
        session_id: _state(run_cli, tmp_path, session_id, session_id + "-lease")
        for session_id in ("wave-1", "wave-2", "wave-3")
    }
    assert states["wave-1"]["terminal_outcome"] == "stale_superseded"
    assert states["wave-2"]["terminal_outcome"] == "stale_superseded"
    assert states["wave-3"]["loop_active"] is True
    assert states["wave-3"]["supersedes"] == ["wave-1", "wave-2"]


def test_supersede_reviews_serializes_discovery_with_separate_process_init(
    run_cli, monkeypatch, tmp_path
):
    common = [
        "init",
        "concurrent review wave",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "concurrent-wave-group",
    ]
    at = _clock()
    old_environment = _env("concurrent-old", "concurrent-old-lease", now=at(0))
    current_environment = _env(
        "concurrent-current", "concurrent-current-lease", now=at(0)
    )
    assert run_cli(*common, cwd=tmp_path, env_extra=old_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=current_environment).returncode == 0
    # 既定 TTL は 15 分。20 分進めて concurrent-old のリースを論理的に期限切れにする。
    late = at(20)

    module = _load_mission_state_module("mission_state_issue543_group_lock")
    entered_locked_body = threading.Event()
    release_locked_body = threading.Event()
    original_locked_body = module._supersede_reviews_locked

    def paused_locked_body(args, cwd):
        entered_locked_body.set()
        assert release_locked_body.wait(timeout=10)
        return original_locked_body(args, cwd)

    monkeypatch.setattr(module, "_supersede_reviews_locked", paused_locked_body)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        **_env("concurrent-current", "concurrent-current-lease", now=late),
        "MISSION_OPERATION_ID": "supersede-concurrent-wave-1",
    }.items():
        monkeypatch.setenv(key, value)
    supersede_errors = []

    def run_supersede():
        try:
            module.cmd_supersede_reviews(
                SimpleNamespace(group="concurrent-wave-group")
            )
        except BaseException as error:  # surface failures in the main thread
            supersede_errors.append(error)

    supersede_thread = threading.Thread(target=run_supersede)
    supersede_thread.start()
    assert entered_locked_body.wait(timeout=10)

    child_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    child_environment.update(_env("concurrent-new", "concurrent-new-lease"))
    init_ready = tmp_path / "concurrent-init-lock-ready"
    concurrent_init = _start_lock_ready_cli(
        common,
        cwd=tmp_path,
        environment=child_environment,
        ready_path=init_ready,
    )
    _wait_for_lock_attempt(concurrent_init, init_ready)
    assert concurrent_init.poll() is None
    release_locked_body.set()
    supersede_thread.join(timeout=10)
    assert not supersede_thread.is_alive()
    assert supersede_errors == []
    child_stdout, child_stderr = concurrent_init.communicate(timeout=10)
    assert concurrent_init.returncode == 0, child_stderr + child_stdout
    assert _state(
        run_cli,
        tmp_path,
        "concurrent-new",
        "concurrent-new-lease",
    )["review_generation"] == 3


def test_supersede_reviews_serializes_discovery_with_review_lineage_set(
    run_cli, monkeypatch, tmp_path
):
    common = [
        "init",
        "review lineage set race",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "set-race-group",
    ]
    at = _clock()
    old_environment = _env("set-race-old", "set-race-old-lease", now=at(0))
    current_environment = _env(
        "set-race-current", "set-race-current-lease", now=at(0)
    )
    joiner_environment = _env("set-race-joiner", "set-race-joiner-lease", now=at(0))
    assert run_cli(*common, cwd=tmp_path, env_extra=old_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=current_environment).returncode == 0
    assert run_cli(
        "init",
        "unrelated session",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=joiner_environment,
    ).returncode == 0
    # 既定 TTL は 15 分。20 分進めて set-race-old のリースを論理的に期限切れにする。
    late = at(20)

    module = _load_mission_state_module("mission_state_issue543_set_group_lock")
    entered_locked_body = threading.Event()
    release_locked_body = threading.Event()
    original_locked_body = module._supersede_reviews_locked

    def paused_locked_body(args, cwd):
        entered_locked_body.set()
        assert release_locked_body.wait(timeout=10)
        return original_locked_body(args, cwd)

    monkeypatch.setattr(module, "_supersede_reviews_locked", paused_locked_body)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        **_env("set-race-current", "set-race-current-lease", now=late),
        "MISSION_OPERATION_ID": "supersede-set-race-group",
    }.items():
        monkeypatch.setenv(key, value)
    supersede_errors = []

    def run_supersede():
        try:
            module.cmd_supersede_reviews(SimpleNamespace(group="set-race-group"))
        except BaseException as error:
            supersede_errors.append(error)

    supersede_thread = threading.Thread(target=run_supersede)
    supersede_thread.start()
    assert entered_locked_body.wait(timeout=10)

    child_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    child_environment.update(joiner_environment)
    set_ready = tmp_path / "concurrent-set-lock-ready"
    concurrent_set = _start_lock_ready_cli(
        [
            "set",
            "review_group_id=set-race-group",
            "review_generation=3",
        ],
        cwd=tmp_path,
        environment=child_environment,
        ready_path=set_ready,
    )
    _wait_for_lock_attempt(concurrent_set, set_ready)
    assert concurrent_set.poll() is None
    release_locked_body.set()
    supersede_thread.join(timeout=10)
    assert not supersede_thread.is_alive()
    assert supersede_errors == []
    set_stdout, set_stderr = concurrent_set.communicate(timeout=10)
    assert concurrent_set.returncode == 0, set_stderr + set_stdout
    joiner = _state(run_cli, tmp_path, "set-race-joiner", "set-race-joiner-lease")
    assert joiner["review_group_id"] == "set-race-group"
    assert joiner["review_generation"] == 3


def test_supersede_reviews_serializes_with_terminal_review_refresh_pid(
    run_cli, monkeypatch, tmp_path
):
    common = [
        "init",
        "terminal refresh race",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "refresh-race-group",
    ]
    at = _clock()
    first_environment = _env("refresh-race-1", "refresh-race-1-lease", now=at(0))
    second_environment = _env("refresh-race-2", "refresh-race-2-lease", now=at(0))
    third_environment = _env("refresh-race-3", "refresh-race-3-lease", now=at(20))
    assert run_cli(*common, cwd=tmp_path, env_extra=first_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=second_environment).returncode == 0
    # 既定 TTL は 15 分。20 分進めて refresh-race-1 のリースを論理的に期限切れにする。
    first_supersede = run_cli(
        "supersede-reviews",
        "--group",
        "refresh-race-group",
        cwd=tmp_path,
        env_extra={
            **second_environment,
            "MISSION_STATE_NOW": at(20),
            "MISSION_OPERATION_ID": "supersede-refresh-race-1",
        },
    )
    assert first_supersede.returncode == 0, first_supersede.stderr
    assert run_cli(*common, cwd=tmp_path, env_extra=third_environment).returncode == 0
    # さらに 20 分進めて refresh-race-2 のリースを期限切れにする。
    late = at(40)

    module = _load_mission_state_module("mission_state_issue543_refresh_group_lock")
    entered_locked_body = threading.Event()
    release_locked_body = threading.Event()
    original_locked_body = module._supersede_reviews_locked

    def paused_locked_body(args, cwd):
        entered_locked_body.set()
        assert release_locked_body.wait(timeout=10)
        return original_locked_body(args, cwd)

    monkeypatch.setattr(module, "_supersede_reviews_locked", paused_locked_body)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        **third_environment,
        "MISSION_STATE_NOW": late,
        "MISSION_OPERATION_ID": "supersede-refresh-race-2",
    }.items():
        monkeypatch.setenv(key, value)
    supersede_errors = []

    def run_supersede():
        try:
            module.cmd_supersede_reviews(
                SimpleNamespace(group="refresh-race-group")
            )
        except BaseException as error:
            supersede_errors.append(error)

    supersede_thread = threading.Thread(target=run_supersede)
    supersede_thread.start()
    assert entered_locked_body.wait(timeout=10)

    child_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    child_environment["MISSION_SESSION_ID"] = "refresh-race-1"
    child_environment["MISSION_FORCE_PID_IS_AGENT"] = "1"
    child_environment["MISSION_STATE_NOW"] = late
    refresh_ready = tmp_path / "concurrent-refresh-lock-ready"
    concurrent_refresh = _start_lock_ready_cli(
        ["refresh-pid", "--force"],
        cwd=tmp_path,
        environment=child_environment,
        ready_path=refresh_ready,
    )
    _wait_for_lock_attempt(concurrent_refresh, refresh_ready)
    assert concurrent_refresh.poll() is None
    release_locked_body.set()
    supersede_thread.join(timeout=10)
    assert not supersede_thread.is_alive()
    assert supersede_errors == []
    refresh_stdout, refresh_stderr = concurrent_refresh.communicate(timeout=10)
    assert concurrent_refresh.returncode == 0, refresh_stderr + refresh_stdout


def test_supersede_reviews_rejects_a_live_old_v5_lease_without_any_write(
    run_cli, tmp_path
):
    common = [
        "init",
        "review generation",
        "--force-mission",
        "--complexity",
        "Standard",
        "--review-group-id",
        "live-review-group",
    ]
    old_environment = _env("old-live", "old-live-lease")
    current_environment = _env("current-live", "current-live-lease")
    assert run_cli(*common, cwd=tmp_path, env_extra=old_environment).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra=current_environment).returncode == 0
    before = {
        session_id: _head(tmp_path, session_id)
        for session_id in ("old-live", "current-live")
    }
    old_state = _state(run_cli, tmp_path, "old-live", "old-live-lease")

    rejected = run_cli(
        "supersede-reviews",
        "--group",
        "live-review-group",
        cwd=tmp_path,
        env_extra={
            **current_environment,
            "MISSION_OPERATION_ID": "supersede-live-old-attempt",
        },
    )

    assert rejected.returncode == 2
    assert (
        "lease held by old-live until "
        + old_state["lease_expires_at"]
    ) in rejected.stderr
    assert {
        session_id: _head(tmp_path, session_id)
        for session_id in ("old-live", "current-live")
    } == before
