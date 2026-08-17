"""Issue #423: invalid-input の自己修復ガイダンスを出す."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
ERROR_GUIDANCE_PY = Path(__file__).resolve().parent.parent / "lib" / "error_guidance.py"


def _load_guidance_module():
    spec = importlib.util.spec_from_file_location("error_guidance", ERROR_GUIDANCE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_review(path: Path, *, perspective: str = "quality", iteration: int = 1) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "mission-review/1",
                "iteration": iteration,
                "perspective": perspective,
                "scores": {
                    "mission_achievement": 4.5,
                    "accuracy": 4.4,
                    "completeness": 4.3,
                    "usability": 4.2,
                },
                "findings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _sidecar_record(state_dir: Path) -> dict:
    telemetry = state_dir / "telemetry" / "command-outcomes"
    sidecars = list(telemetry.glob("*.json"))
    assert len(sidecars) == 1
    return json.loads(sidecars[0].read_text(encoding="utf-8"))["records"][0]


def test_advance_terminal_phase_hint_includes_mark_commands(state_dir, run_cli):
    result = run_cli("advance", "--phase", "halted", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "ERROR: advance で terminal phase へは遷移できません。" in result.stderr
    assert "HINT:" in result.stderr
    assert "mark-passes" in result.stderr
    assert "mark-halt" in result.stderr


def test_advance_activity_format_hint_includes_valid_example(state_dir, run_cli):
    result = run_cli("advance", "--phase", "executing", "--activity", "bad", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response" in result.stderr


def test_advance_missing_canonical_plan_hint(state_dir, run_cli):
    run_cli("set", "planning_policy_version=1", cwd=state_dir.parent, check=True)

    result = run_cli("advance", "--phase", "executing", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "canonical plan" in result.stderr
    assert "plan-import" in result.stderr or "planning" in result.stderr


def test_advance_producing_artifact_hint_shows_both_forms(state_dir, run_cli):
    result = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--artifact-applicability",
        "producing",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "--artifact-applicability not-applicable" in result.stderr
    assert "--artifact-applicability producing --artifact-path <repo-relative-path> --producer-run-id <run-id>" in result.stderr


def test_review_finalize_missing_input_ref_hint_embeds_archive_path(state_dir, run_cli, tmp_path):
    run_cli("init", "test mission", cwd=state_dir.parent, check=True)
    review = _write_review(tmp_path / "review.json")
    imported = run_cli("review-import", "--iteration", "1", "--input", str(review), cwd=state_dir.parent, check=True)
    reference = json.loads(imported.stdout)["review_evidence_ref"]["path"]

    result = run_cli("review-finalize", "--iteration", "1", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stdout
    assert reference in result.stdout
    assert "review-finalize" in result.stdout


def test_review_finalize_min_reviewers_hint_uses_state_value(state_dir, run_cli, tmp_path):
    run_cli("init", "test mission", cwd=state_dir.parent, check=True)
    review = _write_review(tmp_path / "review.json")
    imported = run_cli("review-import", "--iteration", "1", "--input", str(review), cwd=state_dir.parent, check=True)
    reference = json.loads(imported.stdout)["review_evidence_ref"]["path"]

    result = run_cli(
        "review-finalize",
        "--iteration",
        "1",
        "--input-ref",
        reference,
        "--min-reviewers",
        "3",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "HINT:" in result.stdout
    assert "--min-reviewers 2" in result.stdout


def test_review_import_schema_rejection_hint_lists_required_keys(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_text('{"schema":"wrong"}\n', encoding="utf-8")

    result = run_cli("review-import", "--iteration", "1", "--input", str(source), cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "mission-review/1" in result.stderr
    assert "schema" in result.stderr
    assert "--stdin" in result.stderr


def test_lease_mismatch_hint_never_prints_token(state_dir, run_cli):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.update(
        {
            "owner_session_id": "another-session",
            "lease_id": "lease-token-should-not-appear",
            "fencing_epoch": 1,
            "lease_expires_at": "2099-01-01T00:00:00Z",
        }
    )
    state_file.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "set",
        "awaiting_user=true",
        cwd=state_dir.parent,
        env_extra={"MISSION_SESSION_ID": "test", "MISSION_LEASE_ID": "wrong-token"},
    )

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "lease-token-should-not-appear" not in result.stderr
    assert "wrong-token" not in result.stderr
    assert "MISSION_LEASE_ID" in result.stderr


def test_pid_fallback_emits_warning_once(state_dir, run_cli, tmp_path):
    run_cli(
        "init",
        "test mission",
        cwd=state_dir.parent,
        env_extra={
            "MISSION_SESSION_ID": None,
            "CLAUDE_CODE_SESSION_ID": None,
            "CODEX_THREAD_ID": None,
        },
        check=True,
    )
    source = tmp_path / "review.json"
    source.write_text('{"schema":"wrong"}\n', encoding="utf-8")

    result = run_cli(
        "review-import",
        "--iteration",
        "1",
        "--input",
        str(source),
        cwd=state_dir.parent,
        env_extra={
            "MISSION_SESSION_ID": None,
            "CLAUDE_CODE_SESSION_ID": None,
            "CODEX_THREAD_ID": None,
        },
    )

    assert result.returncode == 2
    assert result.stderr.count("WARNING: MISSION_SESSION_ID 未設定のため pid フォールバックを使用") == 1


def test_json_failure_output_contains_guidance_array(state_dir, run_cli, tmp_path):
    run_cli("init", "test mission", cwd=state_dir.parent, check=True)
    source = tmp_path / "review.json"
    source.write_text('{"schema":"wrong"}\n', encoding="utf-8")

    result = run_cli(
        "review-import",
        "--iteration",
        "1",
        "--input",
        str(source),
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["guidance"]
    assert all(line.startswith("HINT:") for line in payload["guidance"])


def test_command_outcome_record_marks_guidance_true(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_text('{"schema":"wrong"}\n', encoding="utf-8")

    result = run_cli(
        "review-import",
        "--iteration",
        "1",
        "--input",
        str(source),
        "--event-id",
        "guidance-record",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert _sidecar_record(state_dir)["guidance"] is True


def test_set_reviewer_count_hint_shows_alternative_example(state_dir, run_cli):
    result = run_cli("set", "reviewer_count=1", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "complexity=Critical reviewer_count=4" in result.stderr or "review_tier=full reviewer_count=4" in result.stderr


def test_set_halt_category_hint_shows_alternative_example(state_dir, run_cli):
    result = run_cli("set", "halt_category=stale", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "mark-halt --reason" in result.stderr


def test_set_halt_reason_hint_shows_reactivate_example(state_dir, run_cli):
    result = run_cli("set", "halt_reason=blocked", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "HINT:" in result.stderr
    assert "reactivate --approved-by-user" in result.stderr


def test_guidance_fallback_when_state_missing():
    mod = _load_guidance_module()
    guidance = mod.build_guidance("review-finalize", "missing-input-ref", {})

    assert guidance
    assert any("<review_evidence_ref.path>" in line or "<input-ref>" in line for line in guidance)
