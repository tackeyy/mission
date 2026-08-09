import json
import hashlib
import os

import pytest


def _write_review(path):
    path.write_text(
        json.dumps(
            {
                "schema": "mission-review/1",
                "perspective": "correctness",
                "iteration": 1,
                "scores": {
                    "mission_achievement": 4.5,
                    "accuracy": 4.4,
                    "completeness": 4.3,
                    "usability": 4.2,
                },
                "findings": [],
                "notes": "portable review fixture",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_init_records_pending_artifact_applicability(run_cli, tmp_path):
    result = run_cli("init", "produce a portable artifact", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["artifact_applicability"] == "pending"


def test_init_accepts_explicit_not_applicable_artifact_contract(run_cli, tmp_path):
    result = run_cli(
        "init",
        "run a mission without an artifact",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["artifact_applicability"] == "not-applicable"


def test_advance_to_reviewing_rejects_pending_artifact_contract(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "artifact applicability is pending" in result.stderr
    assert read_state(state_dir)["phase"] == "executing"


def test_generic_set_cannot_move_pending_artifact_contract_to_reviewing(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("set", "phase=reviewing", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "artifact applicability is pending" in result.stderr
    assert read_state(state_dir)["phase"] == "executing"


def test_artifact_render_persists_canonical_identity(state_dir, run_cli, read_state):
    root = state_dir.parent
    init_result = run_cli("artifact", "init", "--json", cwd=root)
    assert init_result.returncode == 0, init_result.stderr

    render_result = run_cli("artifact", "render", "--json", cwd=root)
    assert render_result.returncode == 0, render_result.stderr

    artifact = read_state(state_dir)["artifact"]
    path = root / artifact["path"]
    payload = path.read_bytes()
    assert artifact == {
        **artifact,
        "path": ".mission-state/artifacts/test/mission-artifact.md",
        "digest": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "producer_run_id": "test",
    }
    assert read_state(state_dir)["artifact_applicability"] == "producing"


def test_aggregate_rejects_artifact_mutated_after_identity_capture(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    artifact = read_state(state_dir)["artifact"]
    (root / artifact["path"]).write_text("# Replaced\nchanged bytes\n", encoding="utf-8")
    review = _write_review(root / "review.json")

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--out",
        str(root / "score.json"),
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "artifact identity does not match recorded state" in result.stderr
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"


def test_mark_passes_rejects_artifact_identity_substitution(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (root / state["artifact"]["path"]).write_text(
        "# substituted artifact\n", encoding="utf-8"
    )

    result = run_cli("mark-passes", cwd=root)

    assert result.returncode == 2
    assert "artifact identity does not match recorded state" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_generic_set_cannot_downgrade_mutated_producing_artifact(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (root / state["artifact"]["path"]).write_text(
        "# changed after clean observation\n", encoding="utf-8"
    )

    downgrade = run_cli(
        "set", "artifact_applicability=not-applicable", cwd=root
    )

    assert downgrade.returncode == 2
    assert "artifact_applicability" in downgrade.stderr
    assert read_state(state_dir)["artifact_applicability"] == "producing"
    mark = run_cli("mark-passes", cwd=root)
    assert mark.returncode == 2
    assert read_state(state_dir)["passes"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("artifact_applicability", "not-applicable"),
        ("artifact", '{"path":"result.md"}'),
        ("artifact_path", "legacy.md"),
        ("artifact_lint", "[]"),
        ("artifact_lint_status", "clean"),
    ],
)
def test_generic_set_freezes_artifact_contract_and_observation_fields(
    key, value, state_dir, run_cli, read_state
):
    before = read_state(state_dir)

    result = run_cli("set", f"{key}={value}", cwd=state_dir.parent)

    assert result.returncode == 2
    assert f"`{key}`" in result.stderr
    assert read_state(state_dir) == before


def test_advance_cannot_downgrade_producing_artifact_to_not_applicable(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    before = read_state(state_dir)

    result = run_cli(
        "advance",
        "--phase",
        "executing",
        "--activity",
        "active:implementation",
        "--artifact-applicability",
        "not-applicable",
        cwd=root,
    )

    assert result.returncode == 2
    assert "cannot downgrade producing" in result.stderr
    assert read_state(state_dir) == before


def test_advance_atomically_records_executor_artifact_handoff(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact_path = root / "reports" / "result.md"
    artifact_path.parent.mkdir()
    artifact_path.write_text("# Result\nverified output\n", encoding="utf-8")

    result = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        "--artifact-applicability",
        "producing",
        "--artifact-path",
        "reports/result.md",
        "--producer-run-id",
        "executor-run-1",
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    recorded = read_state(state_dir)
    assert recorded["phase"] == "reviewing"
    assert recorded["artifact_applicability"] == "producing"
    assert recorded["artifact"] == {
        "path": "reports/result.md",
        "digest": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "size": artifact_path.stat().st_size,
        "producer_run_id": "executor-run-1",
    }


def test_stats_reports_conserved_terminal_artifact_coverage_by_profile(
    run_cli, tmp_path
):
    sessions = tmp_path / "project" / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)

    def write_state(name, applicability, lint_status=None, *, terminal=True):
        state = {
            "mission": f"fixture {name}",
            "mission_id": name,
            "session_id": name,
            "project_root": str(tmp_path / "project"),
            "schema_version": 3,
            "started_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:01:00Z",
            "phase": "done" if terminal else "reviewing",
            "passes": terminal,
            "loop_active": not terminal,
            "terminal_outcome": "completed_pass" if terminal else None,
            "session_role": "implementer",
            "task_profile": {"primary": "portable-analysis"},
            "artifact_applicability": applicability,
        }
        if lint_status is not None:
            state["artifact_lint_status"] = lint_status
            state["artifact_lint"] = (
                []
                if lint_status == "clean"
                else [{"kind": "empty-section", "heading": "Result"}]
            )
        sessions.joinpath(f"{name}.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    write_state("clean", "producing", "clean")
    write_state("findings", "producing", "findings")
    write_state("missing", "producing")
    write_state("invalid", "producing", "invalid")
    write_state("skipped", "not-applicable")
    write_state("active", "producing", "clean", terminal=False)

    result = run_cli("stats", "--root", str(tmp_path / "project"), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    coverage = json.loads(result.stdout)["artifact_coverage"]
    assert coverage["counts"] == {
        "eligible": 4,
        "observed": 2,
        "missing": 1,
        "invalid": 1,
        "clean": 1,
        "findings": 1,
        "skipped": 1,
    }
    assert coverage["coverage"] == 0.5
    assert coverage["counts_conserved"] is True
    assert coverage["by_profile"]["portable-analysis"]["counts"] == coverage["counts"]


def test_stats_text_labels_not_applicable_as_skipped_not_clean(run_cli, tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    sessions.joinpath("skip.json").write_text(
        json.dumps(
            {
                "mission": "non-producing fixture",
                "mission_id": "skip",
                "session_id": "skip",
                "project_root": str(tmp_path),
                "schema_version": 3,
                "started_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-01T00:01:00Z",
                "phase": "done",
                "passes": True,
                "loop_active": False,
                "terminal_outcome": "completed_pass",
                "artifact_applicability": "not-applicable",
            }
        ),
        encoding="utf-8",
    )

    result = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "artifact_coverage:" in result.stdout
    assert "eligible 0 / observed 0 / missing 0 / invalid 0" in result.stdout
    assert "clean 0 / findings 0 / skipped 1" in result.stdout


def test_mark_passes_warns_for_missing_identity_before_profile_reaches_threshold(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["task_profile"] = {"primary": "portable-analysis"}
    state["artifact_applicability"] = "producing"
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    assert "WARN: artifact coverage gate is not active" in result.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_rejects_pending_artifact_contract_defense_in_depth(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["artifact_applicability"] = "pending"
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "artifact applicability is pending" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_gates_missing_identity_after_profile_reaches_threshold(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["task_profile"] = {"primary": "portable-analysis"}
    state["artifact_applicability"] = "producing"
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    historical = {
        **state,
        "session_id": "observed-history",
        "mission_id": "observed-history",
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }
    state_path.with_name("observed-history.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "artifact path is missing" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_aggregate_records_producing_without_identity_as_missing(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["artifact_applicability"] = "producing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    review = _write_review(state_dir.parent / "review.json")

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["artifact_lint_status"] == "missing"
    recorded = read_state(state_dir)
    assert recorded["artifact_lint_status"] == "missing"
    assert "artifact_lint" not in recorded


def test_aggregate_rejects_pending_artifact_contract_defense_in_depth(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "reviewing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "artifact applicability is pending" in result.stderr
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"


def test_review_finalize_inherits_pending_artifact_rejection(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "reviewing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "review-finalize",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        cwd=root,
    )

    assert result.returncode == 2
    assert "artifact applicability is pending" in result.stderr
    assert read_state(state_dir)["score_history"] == []


def test_aggregate_rejects_not_applicable_with_canonical_identity(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["artifact_applicability"] = "not-applicable"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "contradicts canonical artifact identity" in result.stderr
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"


def test_mark_passes_rejects_pending_with_canonical_identity(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["artifact_applicability"] = "pending"
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("mark-passes", cwd=root)

    assert result.returncode == 2
    assert "contradicts canonical artifact identity" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_aggregate_keeps_not_applicable_skipped_even_with_legacy_path(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    artifact = root / "legacy.md"
    artifact.write_text("# Complete\nsubstantive result\n", encoding="utf-8")
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["artifact_applicability"] = "not-applicable"
    state["artifact_path"] = str(artifact)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["artifact_lint_status"] == "skipped"
    recorded = read_state(state_dir)
    assert recorded["artifact_lint_status"] == "skipped"
    assert "artifact_lint" not in recorded


@pytest.mark.parametrize("replacement", ["symlink", "fifo", "invalid-utf8", "oversize"])
def test_canonical_adversarial_artifacts_fail_closed_in_aggregate(
    replacement, state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact_path = root / "result.md"
    artifact_path.write_text("# Result\nportable bytes\n", encoding="utf-8")
    handoff = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        "--artifact-applicability",
        "producing",
        "--artifact-path",
        "result.md",
        "--producer-run-id",
        "executor-run-adversarial",
        cwd=root,
    )
    assert handoff.returncode == 0, handoff.stderr

    artifact_path.unlink()
    if replacement == "symlink":
        target = root / "replacement.md"
        target.write_text("# Replacement\n", encoding="utf-8")
        artifact_path.symlink_to(target)
    elif replacement == "fifo":
        os.mkfifo(artifact_path)
    elif replacement == "invalid-utf8":
        artifact_path.write_bytes(b"\xff\xfe")
    else:
        artifact_path.write_bytes(b"x" * (4 * 1024 * 1024 + 1))

    review = _write_review(root / "review.json")
    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"


def test_canonical_identity_with_invalid_utf8_is_invalid_not_skipped(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (root / "result.md").write_bytes(b"\xff\xfe")
    handoff = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        "--artifact-applicability",
        "producing",
        "--artifact-path",
        "result.md",
        "--producer-run-id",
        "executor-run-invalid-utf8",
        cwd=root,
    )
    assert handoff.returncode == 0, handoff.stderr

    result = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"


def test_active_profile_gate_requires_current_lint_observation(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["task_profile"] = {"primary": "portable-analysis"}
    state["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    historical = {
        **state,
        "session_id": "observed-history",
        "mission_id": "observed-history",
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }
    state_path.with_name("observed-history.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )

    result = run_cli("mark-passes", cwd=root)

    assert result.returncode == 2
    assert "artifact lint observation is missing" in result.stderr
    assert read_state(state_dir)["passes"] is False
