"""Issue #550 C2 Stage B Batch 3 real-process repository coverage.

planning adopt-core / planning promote-provider-plan / manual-score-capture の
repository 移行テスト。実 CLI・別プロセスで検証。

TDD: 各テストは移行前に Red になることを確認した上で実装した。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _env(session_id: str, *, operation_id: str | None = None) -> dict:
    environment = {
        "MISSION_SESSION_ID": session_id,
        "MISSION_LEASE_ID": session_id + "-lease",
    }
    if operation_id is not None:
        environment["MISSION_OPERATION_ID"] = operation_id
    return environment


def _head(root: Path, session_id: str) -> dict:
    return json.loads(
        (root / ".mission-state" / "sessions" / (session_id + ".json")).read_text(
            encoding="utf-8"
        )
    )


def _public_state(run_cli, root: Path, session_id: str) -> dict:
    result = run_cli("get", cwd=root, env_extra=_env(session_id))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _init_v5(run_cli, root: Path, session_id: str) -> None:
    result = run_cli(
        "init",
        "C2 Stage B Batch 3",
        "--complexity",
        "Complex",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=root,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr


def _document() -> dict:
    """Minimal valid mission-plan/1 document."""
    return {
        "objective": "Batch 3 test plan",
        "scope": {
            "resources": [],
            "actions": [{"type": "analyze", "effect_class": "reversible"}],
        },
        "assumptions": [
            {
                "id": "batch3-input",
                "statement": "the isolated batch3 fixture exists",
                "validation": "read it before execution",
            }
        ],
        "steps": [
            {
                "id": "inspect",
                "action": "analyze",
                "inputs": [],
                "outputs": ["finding"],
                "depends_on": [],
                "acceptance_checks": ["state was inspected"],
                "risk": "low",
                "rollback": "none",
            },
            {
                "id": "summarize",
                "action": "write",
                "inputs": ["finding"],
                "outputs": ["summary"],
                "depends_on": ["inspect"],
                "acceptance_checks": ["summary complete"],
                "risk": "low",
                "rollback": "none",
            },
        ],
        "global_acceptance": ["all batch3 checks pass"],
        "stop_conditions": ["fixture unavailable"],
    }


def _write_plan(root: Path, name: str = "plan.json", document: dict | None = None) -> Path:
    path = root / name
    path.write_bytes(_canonical_bytes(document or _document()))
    return path


def _scoring_digest(payload_without_digest: dict) -> str:
    sys.path.insert(0, str(LIB_DIR))
    from scoring_provenance import digest as _digest  # type: ignore[import]

    return _digest(payload_without_digest)


def _make_manual_score_payload(session_id: str, mission_id: str, *, iteration: int = 1) -> dict:
    """Build a valid manual score payload."""
    items = {
        "mission_achievement": 4.5,
        "accuracy": 4.4,
        "completeness": 4.3,
        "usability": 4.2,
    }
    unsigned: dict = {
        "schema": "mission-manual-score/1",
        "session_id": session_id,
        "mission_id": mission_id,
        "iteration": iteration,
        "items": items,
        "composite": 4.35,
        "min_item": 4.2,
        "review_agreement": 4.8,
        "open_high": 0,
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {
            "kind": "manual-source-evidence",
            "ref": "sha256:" + "8" * 64,
            "digest": "sha256:" + "8" * 64,
        },
        "imported_at": "2026-08-18T00:00:00Z",
    }
    return {**unsigned, "input_digest": _scoring_digest(unsigned)}


def _v5_patch_session_state(
    root: Path, session_id: str, patcher: Callable[[dict], None]
) -> None:
    """Patch v5 session state in-process via the V5CompatibilityRepository API."""
    sys.path.insert(0, str(LIB_DIR))
    from mission_persistence.fenced_commit import LocalFencedRepository  # type: ignore[import]
    from mission_persistence.legacy_v4 import V5CompatibilityRepository  # type: ignore[import]

    ms = root / ".mission-state"
    lfr = LocalFencedRepository(ms, lease_ttl_seconds=3600)
    compat = V5CompatibilityRepository(
        repository=lfr,
        session_id=session_id,
        lease_owner_session_id=session_id,
        presented_lease_id=session_id + "-lease",
    )
    with compat.transaction():
        data = compat.load()
        patcher(data)
        compat.save(data)


# ---------------------------------------------------------------------------
# tests: planning adopt-core
# ---------------------------------------------------------------------------


def test_planning_adopt_core_v5_preserves_head_and_replays(run_cli, tmp_path):
    session_id = "batch3-adopt"
    _init_v5(run_cli, tmp_path, session_id)
    plan = _write_plan(tmp_path)
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    first = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "batch3-core-source",
        "--json",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-op-1"),
    )
    assert first.returncode == 0, first.stderr
    out = json.loads(first.stdout)
    assert out["ok"] is True
    canonical_plan = out["canonical_plan"]
    assert isinstance(canonical_plan, dict)
    assert canonical_plan.get("source_id") == "batch3-core-source"
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"

    # Replay: same operation_id must not advance the head
    replay = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "batch3-core-source",
        "--json",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-op-1"),
    )
    assert replay.returncode == 0, replay.stderr
    replay_out = json.loads(replay.stdout)
    assert replay_out["ok"] is True
    assert _head(tmp_path, session_id) == committed

    # Surrounding commands still work after adopt-core
    updated = run_cli(
        "set", "batch3_adopt_probe=true", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert updated.returncode == 0, updated.stderr
    assert _public_state(run_cli, tmp_path, session_id).get("batch3_adopt_probe") is True


def test_planning_adopt_core_v5_requires_operation_id(run_cli, tmp_path):
    session_id = "batch3-adopt-noid"
    _init_v5(run_cli, tmp_path, session_id)
    plan = _write_plan(tmp_path, "plan-noid.json")
    before = _head(tmp_path, session_id)

    result = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "batch3-noid",
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_planning_adopt_core_v5_intent_collision_rejected(run_cli, tmp_path):
    session_id = "batch3-adopt-coll"
    _init_v5(run_cli, tmp_path, session_id)
    plan = _write_plan(tmp_path, "plan-coll.json")

    first = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "collision-source-A",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-collision-id"),
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)

    # Same operation_id but different source_id → intent collision
    collision = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "collision-source-B",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-collision-id"),
    )
    assert collision.returncode == 2
    assert "operation ID has a different intent" in collision.stderr
    assert _head(tmp_path, session_id) == committed


def test_planning_adopt_core_retained_v4_unchanged(legacy_run_cli, tmp_path):
    """v4 セッションでは MISSION_OPERATION_ID 不要で動作すること。"""
    session_id = "batch3-adopt-v4"
    init_result = legacy_run_cli(
        "init",
        "retained v4 adopt-core",
        "--complexity",
        "Complex",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert init_result.returncode == 0, init_result.stderr
    plan = _write_plan(tmp_path, "plan-v4.json")

    result = legacy_run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "v4-source",
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id needed for v4
    )
    assert result.returncode == 0, result.stderr
    state = _head(tmp_path, session_id)
    # v4 state: no schema field, has canonical_plan
    assert "schema" not in state
    assert "canonical_plan" in state
    assert state["canonical_plan"]["source_id"] == "v4-source"


def test_planning_adopt_core_domain_validation_preserved(run_cli, tmp_path):
    """plan contract gate が repository 移行後も保たれること。"""
    session_id = "batch3-adopt-dom"
    _init_v5(run_cli, tmp_path, session_id)

    # Missing required fields – only objective
    bad_plan = tmp_path / "bad-plan.json"
    bad_plan.write_bytes(_canonical_bytes({"objective": "broken"}))
    before = _head(tmp_path, session_id)

    result = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(bad_plan),
        "--source-id",
        "bad-source",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-domain-bad-plan"),
    )
    assert result.returncode != 0
    # Head must not advance on domain rejection
    assert _head(tmp_path, session_id) == before


def test_planning_adopt_core_strategy_gate_preserved(run_cli, tmp_path):
    """planning_strategy != core の場合は planning-strategy-not-core で拒否されること。"""
    session_id = "batch3-adopt-strat"
    _init_v5(run_cli, tmp_path, session_id)
    plan = _write_plan(tmp_path, "plan-strategy.json")

    # Patch planning_strategy to provider-primary
    _v5_patch_session_state(
        tmp_path, session_id, lambda d: d.update({"planning_strategy": "provider-primary"})
    )
    after_patch = _head(tmp_path, session_id)

    result = run_cli(
        "planning",
        "adopt-core",
        "--input",
        str(plan),
        "--source-id",
        "strategy-source",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="adopt-strategy-gate"),
    )
    assert result.returncode != 0
    assert "planning-strategy-not-core" in result.stderr
    assert _head(tmp_path, session_id) == after_patch


# ---------------------------------------------------------------------------
# tests: planning promote-provider-plan
# ---------------------------------------------------------------------------


def test_planning_promote_provider_plan_v5_requires_operation_id(run_cli, tmp_path):
    """v5 セッションで MISSION_OPERATION_ID が未設定の場合 exit 2 になること。"""
    session_id = "batch3-promo-noid"
    _init_v5(run_cli, tmp_path, session_id)
    before = _head(tmp_path, session_id)

    result = run_cli(
        "planning",
        "promote-provider-plan",
        "--invocation-id",
        "inv_" + "a" * 32,
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_planning_promote_provider_plan_domain_gate_preserved(run_cli, tmp_path):
    """planning-policy-not-active gate が repository 移行後も保たれること。"""
    session_id = "batch3-promo-dom"
    _init_v5(run_cli, tmp_path, session_id)
    before = _head(tmp_path, session_id)

    # Attempt promote without provider-primary strategy → domain gate fires
    result = run_cli(
        "planning",
        "promote-provider-plan",
        "--invocation-id",
        "inv_" + "b" * 32,
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="promote-domain-gate"),
    )
    assert result.returncode != 0
    assert "planning-" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_planning_promote_provider_plan_retained_v4_unchanged(legacy_run_cli, tmp_path):
    """v4 セッションで promote-provider-plan が既存通り動作すること。

    v4 は MISSION_OPERATION_ID を要求しない。
    domain gate (strategy) が v4 で変わらず機能していることも確認。
    """
    session_id = "batch3-promo-v4"
    init_result = legacy_run_cli(
        "init",
        "retained v4 promote",
        "--complexity",
        "Complex",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert init_result.returncode == 0, init_result.stderr

    # Without provider-primary strategy → domain error (not MISSION_OPERATION_ID)
    result = legacy_run_cli(
        "planning",
        "promote-provider-plan",
        "--invocation-id",
        "inv_" + "c" * 32,
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id – v4 must not require it
    )
    assert result.returncode != 0
    assert "MISSION_OPERATION_ID" not in result.stderr
    assert "planning-" in result.stderr


# ---------------------------------------------------------------------------
# tests: manual-score-capture
# ---------------------------------------------------------------------------


def test_manual_score_capture_v5_preserves_head_and_replays(run_cli, tmp_path):
    """manual-score-capture が v5 で head を壊さず replay を返すこと。"""
    session_id = "batch3-manual"
    _init_v5(run_cli, tmp_path, session_id)
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    # Patch to iteration=1 in scoring phase
    _v5_patch_session_state(
        tmp_path, session_id, lambda d: d.update({"iteration": 1, "phase": "scoring"})
    )
    state = _public_state(run_cli, tmp_path, session_id)
    mission_id = state.get("mission_id") or "unknown"

    payload = _make_manual_score_payload(session_id, mission_id, iteration=1)
    input_file = tmp_path / "manual-score.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    output_file = tmp_path / "scoring.json"

    first = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(output_file),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="manual-op-1"),
    )
    assert first.returncode == 0, first.stderr
    out = json.loads(first.stdout)
    assert out["ok"] is True
    assert out["scoring_json"] == str(output_file)
    ref = out["manual_evidence_ref"]
    assert ref["kind"] == "manual-score"
    assert ref["digest"].startswith("sha256:")
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"
    assert output_file.exists()

    # Replay: same operation_id must not advance the head
    replay = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(output_file),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="manual-op-1"),
    )
    assert replay.returncode == 0, replay.stderr
    replay_out = json.loads(replay.stdout)
    assert replay_out["ok"] is True
    assert replay_out["manual_evidence_ref"] == ref
    assert _head(tmp_path, session_id) == committed

    # Surrounding commands still work
    updated = run_cli(
        "set", "batch3_manual_probe=true", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert updated.returncode == 0, updated.stderr
    assert _public_state(run_cli, tmp_path, session_id).get("batch3_manual_probe") is True


def test_manual_score_capture_v5_requires_operation_id(run_cli, tmp_path):
    """v5 で MISSION_OPERATION_ID が未設定の場合 exit 2 になること。"""
    session_id = "batch3-manual-noid"
    _init_v5(run_cli, tmp_path, session_id)
    before = _head(tmp_path, session_id)

    # Minimal parseable JSON – the op_id check fires in the preamble, before domain validation
    input_file = tmp_path / "manual-noid.json"
    input_file.write_text(json.dumps({"schema": "mission-manual-score/1"}), encoding="utf-8")

    result = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(tmp_path / "out-noid.json"),
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_manual_score_capture_v5_intent_collision_rejected(run_cli, tmp_path):
    """同一 operation_id で異なる out パスはインテント衝突として拒否されること。"""
    session_id = "batch3-manual-coll"
    _init_v5(run_cli, tmp_path, session_id)
    _v5_patch_session_state(
        tmp_path, session_id, lambda d: d.update({"iteration": 1, "phase": "scoring"})
    )
    state = _public_state(run_cli, tmp_path, session_id)
    mission_id = state.get("mission_id") or "unknown"

    payload = _make_manual_score_payload(session_id, mission_id, iteration=1)
    input_file = tmp_path / "manual-coll.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    output_file_a = tmp_path / "scoring-coll-a.json"
    output_file_b = tmp_path / "scoring-coll-b.json"

    first = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(output_file_a),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="manual-collision-id"),
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)

    # Same operation_id but different --out path → collision
    collision = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(output_file_b),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="manual-collision-id"),
    )
    assert collision.returncode == 2
    assert "operation ID has a different intent" in collision.stderr
    assert _head(tmp_path, session_id) == committed


def test_manual_score_capture_retained_v4_unchanged(legacy_run_cli, tmp_path):
    """v4 セッションで MISSION_OPERATION_ID 不要で動作すること。"""
    session_id = "batch3-manual-v4"
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    initial_state = {
        "mission": "batch3 v4 manual test",
        "mission_id": "abc12345",
        "subtasks": [],
        "complexity": "Standard",
        "reviewer_count": 2,
        "max_iter": 5,
        "threshold": 4.0,
        "iteration": 1,
        "phase": "scoring",
        "score_history": [],
        "stagnation_count": 0,
        "decisions": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-05-25T00:00:00Z",
        "updated_at": "2026-05-25T00:00:00Z",
        "schema_version": 2,
        "project_root": str(tmp_path),
        "pid": 0,
        "hostname": "test",
        "session_id": session_id,
        "created_at_session": "2026-05-25T00:00:00Z",
    }
    (sd / (session_id + ".json")).write_text(json.dumps(initial_state), encoding="utf-8")

    payload = _make_manual_score_payload(session_id, "abc12345", iteration=1)
    input_file = tmp_path / "v4-manual.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    output_file = tmp_path / "v4-scoring.json"

    # No MISSION_OPERATION_ID – v4 must not require it
    result = legacy_run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(output_file),
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": session_id, "MISSION_LEASE_ID": session_id + "-lease"},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert output_file.exists()
    # v4 state: no schema field
    state = json.loads((sd / (session_id + ".json")).read_text(encoding="utf-8"))
    assert "schema" not in state


def test_manual_score_capture_scoring_gate_preserved(run_cli, tmp_path):
    """composite gate が repository 移行後も保たれること。"""
    session_id = "batch3-manual-gate"
    _init_v5(run_cli, tmp_path, session_id)
    _v5_patch_session_state(
        tmp_path, session_id, lambda d: d.update({"iteration": 1, "phase": "scoring"})
    )
    state = _public_state(run_cli, tmp_path, session_id)
    mission_id = state.get("mission_id") or "unknown"
    before = _head(tmp_path, session_id)

    # Invalid composite: far outside valid range; recompute digest so payload is internally consistent
    invalid_payload = _make_manual_score_payload(session_id, mission_id, iteration=1)
    invalid_payload["composite"] = 99.0
    base = {k: v for k, v in invalid_payload.items() if k != "input_digest"}
    invalid_payload["input_digest"] = _scoring_digest(base)

    input_file = tmp_path / "bad-score.json"
    input_file.write_text(json.dumps(invalid_payload), encoding="utf-8")

    result = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(tmp_path / "bad-out.json"),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="gate-op-bad-composite"),
    )
    assert result.returncode != 0
    # Head must not advance on validation failure
    assert _head(tmp_path, session_id) == before


def test_manual_score_capture_open_high_gate_preserved(run_cli, tmp_path):
    """open_high が None の場合も拒否されること。"""
    session_id = "batch3-manual-ohi"
    _init_v5(run_cli, tmp_path, session_id)
    _v5_patch_session_state(
        tmp_path, session_id, lambda d: d.update({"iteration": 1, "phase": "scoring"})
    )
    state = _public_state(run_cli, tmp_path, session_id)
    mission_id = state.get("mission_id") or "unknown"
    before = _head(tmp_path, session_id)

    invalid_payload = _make_manual_score_payload(session_id, mission_id, iteration=1)
    invalid_payload["open_high"] = None  # must be a non-negative int
    base = {k: v for k, v in invalid_payload.items() if k != "input_digest"}
    invalid_payload["input_digest"] = _scoring_digest(base)

    input_file = tmp_path / "openhigh-score.json"
    input_file.write_text(json.dumps(invalid_payload), encoding="utf-8")

    result = run_cli(
        "manual-score-capture",
        "--input",
        str(input_file),
        "--out",
        str(tmp_path / "openhigh-out.json"),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="gate-op-openhigh"),
    )
    assert result.returncode != 0
    assert _head(tmp_path, session_id) == before


# ---------------------------------------------------------------------------
# tests: surrounding commands not broken after batch 3
# ---------------------------------------------------------------------------


def test_surrounding_commands_work_after_batch3_migration(run_cli, tmp_path):
    """get / set / resume / next が Batch 3 移行後も壊れていないこと。"""
    session_id = "batch3-surround"
    _init_v5(run_cli, tmp_path, session_id)

    for cmd, args in [
        ("get", []),
        ("set", ["batch3_surround_probe=true"]),
        ("resume", []),
        ("next", []),
    ]:
        result = run_cli(cmd, *args, cwd=tmp_path, env_extra=_env(session_id))
        assert result.returncode == 0, f"{cmd} {args!r} failed: {result.stderr}"

    assert _public_state(run_cli, tmp_path, session_id).get("batch3_surround_probe") is True


# ---------------------------------------------------------------------------
# tests: allowlist is empty after batch3 (CI enforcement invariant)
# ---------------------------------------------------------------------------


def test_allowlist_is_empty_after_batch3_migration():
    """Batch 3 完了後 C2_DIRECT_WRITE_ALLOWLIST は空。"""
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_ALLOWLIST,
        C2_REPOSITORY_COMMANDS,
    )

    assert C2_DIRECT_WRITE_ALLOWLIST == frozenset(), (
        "C2 migration is incomplete: allowlist is not empty. "
        f"Remaining: {C2_DIRECT_WRITE_ALLOWLIST!r}"
    )
    assert "planning adopt-core" in C2_REPOSITORY_COMMANDS
    assert "planning promote-provider-plan" in C2_REPOSITORY_COMMANDS
    assert "manual-score-capture" in C2_REPOSITORY_COMMANDS


def test_c2_direct_write_functions_is_empty_after_batch3():
    """Batch 3 完了後 C2_DIRECT_WRITE_FUNCTIONS も空であること。"""
    from mission_application.command_owners import C2_DIRECT_WRITE_FUNCTIONS

    assert C2_DIRECT_WRITE_FUNCTIONS == frozenset(), (
        f"C2_DIRECT_WRITE_FUNCTIONS must be empty after Batch 3. "
        f"Found: {C2_DIRECT_WRITE_FUNCTIONS!r}"
    )
