"""Issue #542: new-session v5 cutover and complete lifecycle coverage."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_ROOT = REPO_ROOT / "skills" / "mission"
LIB_DIR = MISSION_ROOT / "lib"
MISSION_STATE_PY = MISSION_ROOT / "bin" / "mission-state.py"
AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"
sys.path.insert(0, str(LIB_DIR))

from mission_application.ports import AuditMetadata, ExecutionRequest  # noqa: E402
from mission_kernel.commands import MarkHalt, encode_kernel_command  # noqa: E402
from mission_kernel.json_codec import decode_json_object  # noqa: E402
from mission_kernel.model import HaltCategory  # noqa: E402
from mission_kernel.transitions import decide  # noqa: E402
from mission_persistence.authoritative_reader import (  # noqa: E402
    read_authoritative_snapshot,
)
from mission_persistence.fenced_commit import (  # noqa: E402
    AdmittedSnapshot,
    FencedCommitError,
    LocalFencedRepository,
    compute_intent_digest,
)
from mission_persistence.local_uow import VerifiedBlobSet  # noqa: E402
from mission_persistence.repository_binding import (  # noqa: E402
    RepositorySelectionError,
    require_legacy_session,
)


FIXED_NOW = "2026-08-18T00:00:00Z"
FIXED_EXPIRY = "2026-08-18T00:15:00Z"
LEASE_CARRIER_PREFIX = "MISSION_LEASE_CARRIER="


@pytest.fixture
def run_cli():
    """Run the unmodified production CLI so C1 observes the real v5 default."""

    def _run(*arguments, cwd, env_extra=None, init_format=None):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MISSION_")
            and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
        }
        if env_extra:
            for key, value in env_extra.items():
                if value is None:
                    environment.pop(key, None)
                else:
                    environment[key] = value
        command = [sys.executable, str(MISSION_STATE_PY), *arguments]
        if init_format == "v4":
            launcher = r'''
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("mission_state_t13_v4", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.NEW_SESSION_REPOSITORY_FORMAT = module.RepositoryFormat.LEGACY_V4
sys.argv = [path] + sys.argv[2:]
module.main()
'''
            command = [
                sys.executable,
                "-c",
                launcher,
                str(MISSION_STATE_PY),
                *arguments,
            ]
        elif init_format not in (None, "v5"):
            raise ValueError("unknown init format: %s" % init_format)
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

    return _run


def _load_mission_state_module(name: str):
    spec = importlib.util.spec_from_file_location(name, MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env(session_id: str = "test") -> dict[str, str]:
    return {
        "MISSION_SESSION_ID": session_id,
        "MISSION_LEASE_ID": "%s-lease" % session_id,
        "MISSION_STATE_NOW": FIXED_NOW,
    }


def _init_v5(run_cli, root: Path, *, session_id: str = "test"):
    result = run_cli(
        "init",
        "Issue 542 lifecycle",
        "--complexity",
        "Simple",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=root,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr
    return result


def _lease_carrier(stderr: str) -> dict:
    carriers = [
        json.loads(line[len(LEASE_CARRIER_PREFIX) :])
        for line in stderr.splitlines()
        if line.startswith(LEASE_CARRIER_PREFIX)
    ]
    assert len(carriers) == 1, stderr
    return carriers[0]


def _head(root: Path, session_id: str = "test") -> dict:
    return json.loads(
        (root / ".mission-state" / "sessions" / (session_id + ".json")).read_text(
            encoding="utf-8"
        )
    )


def _state(root: Path, session_id: str = "test") -> dict:
    snapshot = read_authoritative_snapshot(
        root / ".mission-state" / "sessions" / (session_id + ".json"),
        expected_session_id=session_id,
    )
    return snapshot.document_copy()


def _review(path: Path, perspective: str, scores: tuple[float, float, float, float]):
    payload = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": 1,
        "scores": dict(
            zip(
                ("mission_achievement", "accuracy", "completeness", "usability"),
                scores,
            )
        ),
        "findings": [],
        "same_score_note": None,
        "notes": "%s review" % perspective,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _genesis_state_bytes(session_id: str = "test", lease_id: str = "test-lease") -> bytes:
    return json.dumps(
        {
            "schema_version": 4,
            "mission": "Issue 542 genesis",
            "mission_id": "issue542",
            "session_id": session_id,
            "phase": "planning",
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "threshold": 4.0,
            "iteration": 0,
            "owner_session_id": session_id,
            "lease_id": lease_id,
            "fencing_epoch": 1,
            "lease_expires_at": FIXED_EXPIRY,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _genesis_request(
    *,
    session_id: str = "test",
    lease_id: str = "test-lease",
    operation_id: str = "init-test-operation",
    mission: str = "Issue 542 genesis",
) -> ExecutionRequest:
    command = decode_json_object(
        json.dumps(
            {
                "schema": "mission-command-intent/1",
                "type": "init",
                "value": {"mission": mission},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    blobs = VerifiedBlobSet(())
    return ExecutionRequest(
        session_id=session_id,
        lease_owner_session_id=session_id,
        command=command,
        blobs=blobs,
        operation_id=operation_id,
        intent_digest=compute_intent_digest(
            session_id=session_id,
            lease_owner_session_id=session_id,
            operation_id=operation_id,
            command=command,
            blobs=blobs,
        ),
        presented_lease_id=lease_id,
        audit=AuditMetadata("init", ()),
    )


def test_t3_new_v5_session_completes_the_full_cli_lifecycle(tmp_path, run_cli):
    """T3 is intentionally first: a cutover is useful only when closeout completes."""
    _init_v5(run_cli, tmp_path)
    commands = [
        ("set", "planning_policy_version=0"),
        ("advance", "--phase", "executing", "--artifact-applicability", "not-applicable"),
        ("activity", "start", "--kind", "active", "--reason", "implementation"),
        ("activity", "end"),
        ("advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable"),
    ]
    for command in commands:
        result = run_cli(*command, cwd=tmp_path, env_extra=_env())
        assert result.returncode == 0, (command, result.stdout, result.stderr)

    review_a = _review(tmp_path / "a.json", "A", (4.6, 4.4, 4.2, 4.0))
    review_b = _review(tmp_path / "b.json", "B", (4.4, 4.2, 4.0, 3.8))
    imported_a = run_cli(
        "review-import", "--iteration", "1", "--input", str(review_a),
        cwd=tmp_path, env_extra=_env(),
    )
    assert imported_a.returncode == 0, imported_a.stderr
    imported_b = run_cli(
        "review-import", "--iteration", "1", "--input", str(review_b),
        cwd=tmp_path, env_extra=_env(),
    )
    assert imported_b.returncode == 0, imported_b.stderr
    reference_a = json.loads(imported_a.stdout)["review_evidence_ref"]["path"]
    reference_b = json.loads(imported_b.stdout)["review_evidence_ref"]["path"]
    finalized = run_cli(
        "review-finalize", "--iteration", "1",
        "--input-ref", reference_a, "--input-ref", reference_b,
        "--min-reviewers", "2",
        "--reviewer-window", "A=2026-08-18T00:00:00Z..2026-08-18T00:05:00Z",
        "--reviewer-window", "B=2026-08-18T00:00:30Z..2026-08-18T00:04:00Z",
        cwd=tmp_path, env_extra=_env(),
    )
    assert finalized.returncode == 0, finalized.stderr
    passed = run_cli("mark-passes", cwd=tmp_path, env_extra=_env())
    assert passed.returncode == 0, passed.stderr
    closed = run_cli("closeout", cwd=tmp_path, env_extra=_env())
    assert closed.returncode == 0, closed.stderr

    final_state = _state(tmp_path)
    assert final_state["passes"] is True
    assert final_state["loop_active"] is False
    assert final_state["phase"] == "done"
    assert _head(tmp_path)["schema"] == "mission-head/1"


def test_v5_takeover_emits_carrier_for_the_next_independent_process(
    tmp_path,
    run_cli,
):
    without_token = {
        "MISSION_SESSION_ID": "test",
        "MISSION_LEASE_ID": None,
        "MISSION_STATE_NOW": FIXED_NOW,
    }
    initialized = run_cli(
        "init",
        "Issue 542 lease takeover",
        "--complexity",
        "Simple",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
        env_extra=without_token,
    )
    assert initialized.returncode == 0, initialized.stderr

    takeover = run_cli(
        "set",
        "complexity=Complex",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": "test",
            "MISSION_LEASE_ID": None,
            "MISSION_STATE_NOW": "2026-08-18T00:16:00Z",
        },
    )
    assert takeover.returncode == 0, takeover.stderr
    carrier = _lease_carrier(takeover.stderr)
    assert carrier["schema"] == "mission-lease-carrier/1"
    assert carrier["action"] == "taken-over"
    assert carrier["session_id"] == "test"
    assert carrier["fencing_epoch"] == 2

    continued = run_cli(
        "set",
        "complexity=Simple",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": "test",
            "MISSION_LEASE_ID": carrier["lease_id"],
            "MISSION_STATE_NOW": "2026-08-18T00:16:01Z",
        },
    )
    assert continued.returncode == 0, continued.stderr
    assert _state(tmp_path)["complexity"] == "Simple"


def test_t1_new_init_creates_v5_head_commit_generation_and_object(tmp_path, run_cli):
    _init_v5(run_cli, tmp_path)

    head = _head(tmp_path)
    state_root = tmp_path / ".mission-state"
    assert head["schema"] == "mission-head/1"
    assert list((state_root / "commits").glob("*.json"))
    assert list((state_root / "generations").glob("*.json"))
    assert list((state_root / "objects").glob("*.blob"))
    assert _state(tmp_path)["schema_version"] == 4


def test_t2_existing_v4_session_stays_on_v4_writer(tmp_path, run_cli, state_dir):
    new_root = tmp_path / "new-session-control"
    new_root.mkdir()
    _init_v5(run_cli, new_root, session_id="new")
    assert _head(new_root, "new")["schema"] == "mission-head/1"

    legacy_path = state_dir / "sessions" / "test.json"
    before = json.loads(legacy_path.read_text(encoding="utf-8"))
    result = run_cli(
        "set", "complexity=Complex", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "test", "MISSION_LEASE_ID": None},
    )
    assert result.returncode == 0, result.stderr
    after = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert "schema" not in after
    assert after["mission_id"] == before["mission_id"]
    assert after["complexity"] == "Complex"
    assert not list((state_dir / "commits").glob("*.json"))


def test_t4_reinit_of_v5_session_is_rejected(tmp_path, run_cli):
    _init_v5(run_cli, tmp_path)
    before = (tmp_path / ".mission-state" / "sessions" / "test.json").read_bytes()

    second = run_cli(
        "init", "different mission", "--complexity", "Simple", "--force-mission",
        cwd=tmp_path, env_extra=_env(),
    )

    assert second.returncode == 2
    assert "session-already-initialized" in second.stderr
    assert (tmp_path / ".mission-state" / "sessions" / "test.json").read_bytes() == before


def test_t5_real_process_crash_after_genesis_head_replays_original_result(tmp_path):
    repository_root = tmp_path / ".mission-state"
    script = r'''
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from test_issue542_c1_core import _genesis_request, _genesis_state_bytes
from mission_persistence.fenced_commit import LocalFencedRepository

def kill(point):
    if point == "after-head-replace":
        os._exit(91)

repository = LocalFencedRepository(
    Path(sys.argv[1]),
    clock=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
    fault_injector=kill,
)
repository.initialize(_genesis_request(), state_bytes=_genesis_state_bytes())
raise AssertionError("fault point was not reached")
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(LIB_DIR), str(Path(__file__).parent)))
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(repository_root)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert crashed.returncode == 91, crashed.stderr

    recovered = LocalFencedRepository(
        repository_root,
        clock=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    replay = recovered.initialize(
        _genesis_request(), state_bytes=_genesis_state_bytes()
    )
    assert replay == recovered.read("test").result
    assert replay.generation == 1
    assert len(list((repository_root / "commits").glob("*.json"))) == 1


def test_t6_genesis_operation_id_reuse_with_different_intent_collides(tmp_path):
    repository = LocalFencedRepository(
        tmp_path / ".mission-state",
        clock=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    repository.initialize(_genesis_request(), state_bytes=_genesis_state_bytes())

    with pytest.raises(FencedCommitError) as collision:
        repository.initialize(
            _genesis_request(mission="different intent"),
            state_bytes=_genesis_state_bytes(),
        )

    assert collision.value.code == "operation-intent-collision"


def test_t7_initialize_is_only_genesis_route_and_stage_execute_still_reject(tmp_path):
    repository = LocalFencedRepository(
        tmp_path / ".mission-state",
        clock=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert callable(repository.initialize)
    request = _genesis_request()
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is None
    command = MarkHalt(HaltCategory.OTHER, "not a genesis route")
    typed_request = replace(
        request,
        command=encode_kernel_command(command),
        typed_command=command,
        audit=AuditMetadata("mark-halt", ("mission-halted",)),
    )
    typed_request = replace(
        typed_request,
        intent_digest=compute_intent_digest(
            session_id=typed_request.session_id,
            lease_owner_session_id=typed_request.lease_owner_session_id,
            operation_id=typed_request.operation_id,
            command=typed_request.command,
            blobs=typed_request.blobs,
        ),
    )
    admitted = repository.begin(typed_request)
    target = json.loads(_genesis_state_bytes())
    target["halt_reason"] = "not a genesis route"
    target["halt_category"] = "other"
    target["phase"] = "halted"
    target["loop_active"] = False
    state = repository._stage_persistence  # initialize must exist before private seam is reachable
    assert callable(state)
    decision_state = replace(
        __import__("mission_kernel").decode_mission_state(_genesis_state_bytes()),
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    transition = decide(decision_state, command).transition
    assert transition is not None
    with pytest.raises(FencedCommitError) as stage_rejection:
        repository.stage(admitted, transition, typed_request.blobs)
    assert stage_rejection.value.code == "transition-binding-mismatch"
    executed = repository.execute(typed_request)
    assert executed.accepted is False
    assert executed.rejection_code == "initial-state-required"


def test_t8_v5_session_has_one_writer_and_stays_format_pinned(tmp_path, run_cli):
    _init_v5(run_cli, tmp_path)
    for value in ("Standard", "Complex"):
        result = run_cli(
            "set", "complexity=%s" % value, cwd=tmp_path, env_extra=_env()
        )
        assert result.returncode == 0, result.stderr
        assert _head(tmp_path)["schema"] == "mission-head/1"
    assert _head(tmp_path)["generation"] == 3
    assert not (tmp_path / ".mission-state" / "state.json").exists()
    assert len(list((tmp_path / ".mission-state" / "sessions").glob("test.json"))) == 1


def test_t9_v4_reader_rejects_new_v5_head_fail_closed(tmp_path, run_cli):
    _init_v5(run_cli, tmp_path)
    session_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    with pytest.raises(RepositorySelectionError) as rejected:
        require_legacy_session("test", session_path).select()
    assert rejected.value.code == "repository-format-v5-requires-uow"


def test_t10_one_default_change_rolls_new_init_back_to_v4_but_reads_existing_v5(
    tmp_path, monkeypatch,
):
    module = _load_mission_state_module("mission_state_issue542_rollback")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "v5-existing")
    monkeypatch.setenv("MISSION_LEASE_ID", "v5-existing-lease")
    monkeypatch.setenv("MISSION_STATE_NOW", FIXED_NOW)
    args = module._build_parser().parse_args(
        ["init", "existing v5", "--complexity", "Simple", "--force-mission"]
    )
    args.func(args)
    assert _head(tmp_path, "v5-existing")["schema"] == "mission-head/1"

    monkeypatch.setattr(
        module,
        "NEW_SESSION_REPOSITORY_FORMAT",
        module.RepositoryFormat.LEGACY_V4,
    )
    monkeypatch.setenv("MISSION_SESSION_ID", "v4-after-rollback")
    monkeypatch.setenv("MISSION_LEASE_ID", "v4-after-rollback-lease")
    rolled_back = module._build_parser().parse_args(
        ["init", "new v4", "--complexity", "Simple", "--force-mission"]
    )
    rolled_back.func(rolled_back)

    assert "schema" not in _head(tmp_path, "v4-after-rollback")
    assert _state(tmp_path, "v5-existing")["mission"] == "existing v5"


def test_t11_mixed_root_supports_stats_audit_list_and_next(tmp_path, run_cli, state_dir):
    legacy_path = state_dir / "sessions" / "legacy.json"
    legacy = json.loads((state_dir / "sessions" / "test.json").read_text())
    legacy["session_id"] = "legacy"
    legacy["mission_id"] = "legacy-id"
    (state_dir / "sessions" / "test.json").unlink()
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    _init_v5(run_cli, tmp_path, session_id="current")

    stats = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    listed = run_cli(
        "list",
        cwd=tmp_path,
        env_extra={"MISSION_SEARCH_ROOTS": str(tmp_path)},
    )
    next_action = run_cli("next", cwd=tmp_path, env_extra=_env("current"))
    audit = subprocess.run(
        [sys.executable, str(AUDIT_PY), "--root", str(tmp_path), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stats.returncode == audit.returncode == listed.returncode == next_action.returncode == 0
    assert json.loads(stats.stdout)["total_sessions"] == 2
    assert json.loads(audit.stdout)["total_sessions"] == 2
    assert json.loads(next_action.stdout)["session_id"] == "current"


def test_t12_distribution_and_python39_gates_cover_cutover_modules():
    paths = (
        MISSION_ROOT / "bin" / "mission-state.py",
        MISSION_ROOT / "lib" / "mission_persistence" / "fenced_commit.py",
        MISSION_ROOT / "lib" / "mission_persistence" / "repository_binding.py",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 9))
        mirror = REPO_ROOT / "plugins" / "mission" / path.relative_to(REPO_ROOT)
        assert mirror.read_bytes() == path.read_bytes()
    fenced_source = paths[1].read_text(encoding="utf-8")
    assert "def initialize(" in fenced_source
    cli_source = paths[0].read_text(encoding="utf-8")
    assert "NEW_SESSION_REPOSITORY_FORMAT = RepositoryFormat.V5" in cli_source


def _exercise_t13_independent_cli_contract(run_cli, root: Path, repository_format: str):
    root.mkdir()
    session_id = "t13-%s" % repository_format
    initialized = run_cli(
        "init",
        "Issue 542 T13 independent CLI contract",
        "--complexity",
        "Simple",
        "--issue-ref",
        "542",
        cwd=root,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": FIXED_NOW,
        },
        init_format=repository_format,
    )
    assert initialized.returncode == 0, initialized.stderr

    phase = run_cli(
        "get",
        "--field",
        "phase",
        cwd=root,
        env_extra={"MISSION_SESSION_ID": session_id},
    )
    assert phase.returncode == 0, phase.stderr
    assert json.loads(phase.stdout) == "planning"

    lease = run_cli(
        "get",
        "--field",
        "lease_id",
        cwd=root,
        env_extra={"MISSION_SESSION_ID": session_id},
    )
    assert lease.returncode == 0, lease.stderr
    lease_id = json.loads(lease.stdout)
    assert isinstance(lease_id, str) and lease_id

    changed = run_cli(
        "set",
        "complexity=Standard",
        cwd=root,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_LEASE_ID": lease_id,
            "MISSION_STATE_NOW": FIXED_NOW,
        },
    )
    assert changed.returncode == 0, (changed.stdout, changed.stderr)
    assert json.loads(changed.stdout)["ok"] is True

    halted = run_cli(
        "mark-halt",
        "--reason",
        "T13 stale recovery",
        "--category",
        "stale",
        cwd=root,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_LEASE_ID": lease_id,
            "MISSION_STATE_NOW": "2026-08-18T00:00:02Z",
        },
    )
    assert halted.returncode == 0, (halted.stdout, halted.stderr)

    resumed = run_cli(
        "resume",
        cwd=root,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": "2026-08-18T00:16:03Z",
        },
    )
    assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
    resumed_output = json.loads(resumed.stdout)
    assert isinstance(resumed_output.get("next_action"), str)
    assert resumed_output["resume"]["reactivated"] is True
    resume_carrier = _lease_carrier(resumed.stderr)
    assert resume_carrier["action"] == "taken-over"
    assert resume_carrier["fencing_epoch"] == 2

    public = run_cli(
        "get",
        cwd=root,
        env_extra={"MISSION_SESSION_ID": session_id},
    )
    assert public.returncode == 0, public.stderr
    public_state = json.loads(public.stdout)
    assert public_state["complexity"] == "Standard"
    assert public_state["loop_active"] is True
    assert public_state["halt_reason"] == ""
    assert public_state["fencing_epoch"] == 2
    assert not {"commit", "generation", "schema"}.intersection(public_state)
    return public_state, resumed_output


def _stable_t13_resume_output(output: dict) -> dict:
    return {
        key: output[key]
        for key in (
            "next_action",
            "summary",
            "command_hint",
            "details",
            "phase",
            "iteration",
            "loop_active",
            "passes",
            "stagnation_count",
            "resume",
        )
    }


@pytest.mark.parametrize("repository_format", ("v4", "v5"))
def test_t13_independent_cli_processes_preserve_the_v4_public_contract(
    tmp_path,
    run_cli,
    repository_format,
):
    actual_state, actual_resume = _exercise_t13_independent_cli_contract(
        run_cli,
        tmp_path / "actual",
        repository_format,
    )
    v4_state, v4_resume = _exercise_t13_independent_cli_contract(
        run_cli,
        tmp_path / "v4-reference",
        "v4",
    )
    assert set(actual_state) == set(v4_state)
    assert _stable_t13_resume_output(actual_resume) == _stable_t13_resume_output(
        v4_resume
    )


@pytest.mark.parametrize("repository_format", ("v4", "v5"))
def test_t14_resume_accepts_the_init_lease_in_an_independent_process(
    tmp_path,
    run_cli,
    repository_format,
):
    session_id = "t14-%s" % repository_format
    initialized = run_cli(
        "init",
        "Issue 542 T14 lease carrier resume",
        "--complexity",
        "Simple",
        "--force-mission",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": FIXED_NOW,
        },
        init_format=repository_format,
    )
    assert initialized.returncode == 0, initialized.stderr
    lease_id = _lease_carrier(initialized.stderr)["lease_id"]

    resumed = run_cli(
        "resume",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_LEASE_ID": lease_id,
            "MISSION_STATE_NOW": "2026-08-18T00:00:01Z",
        },
    )

    assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
    output = json.loads(resumed.stdout)
    assert isinstance(output.get("next_action"), str)
    assert output["resume"]["pid_refreshed"] is True


@pytest.mark.parametrize("repository_format", ("v4", "v5"))
def test_t15_resume_without_a_lease_is_a_diagnosed_cli_rejection(
    tmp_path,
    run_cli,
    repository_format,
):
    session_id = "t15-%s" % repository_format
    initialized = run_cli(
        "init",
        "Issue 542 T15 tokenless resume rejection",
        "--complexity",
        "Simple",
        "--force-mission",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": FIXED_NOW,
        },
        init_format=repository_format,
    )
    assert initialized.returncode == 0, initialized.stderr

    resumed = run_cli(
        "resume",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": "2026-08-18T00:00:01Z",
        },
    )

    assert resumed.returncode == 2, (resumed.stdout, resumed.stderr)
    assert resumed.stderr.startswith("ERROR: lease held by %s until " % session_id)
    assert "MISSION_LEASE_ID" in resumed.stderr


@pytest.mark.parametrize("repository_format", ("v4", "v5"))
@pytest.mark.parametrize(
    "command",
    (
        ("refresh-pid",),
        (
            "mark-halt",
            "--reason",
            "Issue 542 active lease rejection",
            "--category",
            "stale",
        ),
    ),
)
def test_active_lease_rejections_keep_the_v4_cli_diagnostic(
    tmp_path,
    run_cli,
    repository_format,
    command,
):
    session_id = "lease-rejection-%s-%s" % (repository_format, command[0])
    initialized = run_cli(
        "init",
        "Issue 542 mutating command lease rejection",
        "--complexity",
        "Simple",
        "--force-mission",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": FIXED_NOW,
        },
        init_format=repository_format,
    )
    assert initialized.returncode == 0, initialized.stderr

    rejected = run_cli(
        *command,
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_STATE_NOW": "2026-08-18T00:00:01Z",
        },
    )

    assert rejected.returncode == 2, (rejected.stdout, rejected.stderr)
    assert rejected.stderr.startswith("ERROR: lease held by %s until " % session_id)
    assert "MISSION_LEASE_ID" in rejected.stderr
