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


def test_artifact_render_atomically_invalidates_prior_lint_observation(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    observed = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )
    assert observed.returncode == 0, observed.stderr
    assert read_state(state_dir)["artifact_lint_status"] == "clean"
    assert "artifact_lint_identity" in read_state(state_dir)

    regenerated = run_cli("artifact", "render", "--json", cwd=root)

    assert regenerated.returncode == 0, regenerated.stderr
    state = read_state(state_dir)
    assert "artifact_lint_status" not in state
    assert "artifact_lint" not in state
    assert "artifact_lint_identity" not in state


def test_artifact_append_invalidates_prior_lint_observation_before_rerender(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    observed = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )
    assert observed.returncode == 0, observed.stderr

    appended = run_cli(
        "artifact",
        "append",
        "--section",
        "evidence",
        "--text",
        "new producer evidence",
        "--json",
        cwd=root,
    )

    assert appended.returncode == 0, appended.stderr
    state = read_state(state_dir)
    assert "artifact_lint_status" not in state
    assert "artifact_lint" not in state
    assert "artifact_lint_identity" not in state


@pytest.mark.parametrize(
    "producer_args",
    [
        ("artifact", "init", "--json"),
        (
            "artifact",
            "export",
            "--to",
            "reports/exported-result.md",
            "--redaction-status",
            "reviewed",
            "--json",
        ),
        (
            "artifact",
            "publish",
            "--provider",
            "local",
            "--require-confirm",
            "--approval-text",
            "portable approval evidence",
            "--json",
        ),
    ],
)
def test_artifact_producers_invalidate_observation_bound_to_prior_identity(
    producer_args, state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert (
        run_cli(
            "artifact",
            "render",
            "--redaction-status",
            "reviewed",
            "--json",
            cwd=root,
        ).returncode
        == 0
    )
    observed = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(_write_review(root / "review.json")),
        "--json",
        cwd=root,
    )
    assert observed.returncode == 0, observed.stderr

    produced = run_cli(*producer_args, cwd=root)

    assert produced.returncode == 0, produced.stderr
    state = read_state(state_dir)
    assert "artifact_lint_status" not in state
    assert "artifact_lint" not in state
    assert "artifact_lint_identity" not in state


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


def test_planning_projection_and_nested_artifact_identity_coexist_fail_closed(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(
        {
            "phase": "executing",
            "complexity": "Complex",
            "artifact_applicability": "pending",
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = root / "planning-providers.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    recommendation = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Coordinate a multi-step architecture change",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--installed-skills",
        "deep-planning-provider",
        "--record-state",
        "--json",
        cwd=root,
    )
    assert recommendation.returncode == 0, recommendation.stderr
    selected_before = read_state(state_dir)["specialists_selected"]
    projection_before = read_state(state_dir)["specialist_registry_projection"]

    artifact_path = root / "reports" / "nested" / "result.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Result\nverified output\n", encoding="utf-8")
    handoff = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        "--artifact-applicability",
        "producing",
        "--artifact-path",
        "reports/nested/result.md",
        "--producer-run-id",
        "executor-run-nested",
        cwd=root,
    )
    assert handoff.returncode == 0, handoff.stderr
    recorded = read_state(state_dir)
    assert recorded["specialists_selected"] == selected_before
    assert recorded["specialist_registry_projection"] == projection_before
    assert recorded["artifact"] == {
        "path": "reports/nested/result.md",
        "digest": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "size": artifact_path.stat().st_size,
        "producer_run_id": "executor-run-nested",
    }

    review = _write_review(root / "review.json")
    observed = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert observed.returncode == 0, observed.stderr
    observed_state = read_state(state_dir)
    assert observed_state["artifact_lint_status"] == "clean"
    assert observed_state["artifact_lint_identity"] == observed_state["artifact"]
    assert observed_state["specialists_selected"] == selected_before
    assert observed_state["specialist_registry_projection"] == projection_before

    artifact_path.write_text("# Mutated\nunbound bytes\n", encoding="utf-8")
    rejected = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert rejected.returncode == 2
    assert "artifact identity does not match recorded state" in rejected.stderr
    rejected_state = read_state(state_dir)
    assert rejected_state["artifact_lint_status"] == "invalid"
    assert rejected_state["specialists_selected"] == selected_before
    assert rejected_state["specialist_registry_projection"] == projection_before


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
            if lint_status in {"clean", "findings"}:
                identity = {
                    "path": f"reports/{name}.md",
                    "digest": hashlib.sha256(name.encode()).hexdigest(),
                    "size": len(name),
                    "producer_run_id": f"portable-{name}",
                }
                state["artifact"] = identity
                state["artifact_lint_identity"] = identity
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


def test_stats_json_and_text_keep_not_applicable_identity_contradiction_invalid(
    run_cli, tmp_path
):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    base = {
        "mission": "portable coverage fixture",
        "project_root": str(tmp_path),
        "schema_version": 3,
        "started_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:01:00Z",
        "phase": "done",
        "passes": False,
        "loop_active": False,
        "terminal_outcome": "failed",
        "task_profile": {"primary": "portable-analysis"},
        "artifact_applicability": "not-applicable",
    }
    contradiction = {
        **base,
        "mission_id": "contradiction",
        "session_id": "contradiction",
        "artifact": {"path": ["malformed"]},
        "artifact_lint_status": "clean",
        "artifact_lint_identity": {
            "path": "reports/stale.md",
            "digest": "a" * 64,
            "size": 12,
            "producer_run_id": "stale-run",
        },
    }
    skipped = {
        **base,
        "mission_id": "skipped",
        "session_id": "skipped",
    }
    forged_producing = {
        **base,
        "mission_id": "forged-producing",
        "session_id": "forged-producing",
        "artifact_applicability": "producing",
        "artifact_lint_status": "findings",
        "artifact_lint": [{"kind": "unverified"}],
    }
    sessions.joinpath("contradiction.json").write_text(
        json.dumps(contradiction), encoding="utf-8"
    )
    sessions.joinpath("skipped.json").write_text(
        json.dumps(skipped), encoding="utf-8"
    )
    sessions.joinpath("forged-producing.json").write_text(
        json.dumps(forged_producing), encoding="utf-8"
    )

    json_result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    text_result = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path)

    assert json_result.returncode == 0, json_result.stderr
    assert text_result.returncode == 0, text_result.stderr
    coverage = json.loads(json_result.stdout)["artifact_coverage"]
    assert coverage["counts"] == {
        "eligible": 2,
        "observed": 0,
        "missing": 0,
        "invalid": 2,
        "clean": 0,
        "findings": 0,
        "skipped": 1,
    }
    assert coverage["counts_conserved"] is True
    assert coverage["by_profile"]["portable-analysis"]["counts"] == coverage["counts"]
    assert coverage["by_terminal_outcome"]["failed"]["counts"] == coverage["counts"]
    assert "eligible 2 / observed 0 / missing 0 / invalid 2" in text_result.stdout
    assert "clean 0 / findings 0 / skipped 1" in text_result.stdout


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
        "artifact": {
            "path": "reports/observed-history.md",
            "digest": "a" * 64,
            "size": 12,
            "producer_run_id": "portable-history",
        },
        "artifact_lint_identity": {
            "path": "reports/observed-history.md",
            "digest": "a" * 64,
            "size": 12,
            "producer_run_id": "portable-history",
        },
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


def test_invalid_utf8_recapture_requires_a_new_identity_bound_lint_observation(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state["task_profile"] = {"primary": "portable-analysis"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact_path = root / "result.md"
    artifact_path.write_text("# Result\nverified output\n", encoding="utf-8")
    first_handoff = run_cli(
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
        "portable-run-1",
        cwd=root,
    )
    assert first_handoff.returncode == 0, first_handoff.stderr
    review = _write_review(root / "review.json")
    first_observation = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert first_observation.returncode == 0, first_observation.stderr
    historical = read_state(state_dir)
    historical.update(
        {
            "session_id": "observed-history",
            "mission_id": "observed-history",
            "phase": "done",
            "passes": True,
            "loop_active": False,
            "terminal_outcome": "completed_pass",
        }
    )
    state_path.with_name("observed-history.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )

    artifact_path.write_bytes(b"\xff\xfe")
    invalid_handoff = run_cli(
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
        "portable-run-invalid",
        cwd=root,
    )
    assert invalid_handoff.returncode == 0, invalid_handoff.stderr
    invalid_observation = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert invalid_observation.returncode == 2
    assert read_state(state_dir)["artifact_lint_status"] == "invalid"

    artifact_path.write_text("# Result\nrecovered output\n", encoding="utf-8")
    recovered_handoff = run_cli(
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
        "portable-run-recovered",
        cwd=root,
    )
    assert recovered_handoff.returncode == 0, recovered_handoff.stderr
    recovered = read_state(state_dir)
    assert "artifact_lint_status" not in recovered
    recovered["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(recovered), encoding="utf-8")

    blocked = run_cli("mark-passes", cwd=root)
    assert blocked.returncode == 2
    assert "artifact lint observation" in blocked.stderr

    fresh_observation = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert fresh_observation.returncode == 0, fresh_observation.stderr
    observed_state = read_state(state_dir)
    assert observed_state["artifact_lint_identity"] == observed_state["artifact"]

    passed = run_cli("mark-passes", cwd=root)
    assert passed.returncode == 0, passed.stderr
    assert read_state(state_dir)["passes"] is True


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
        "artifact_lint_identity": {
            key: state["artifact"][key]
            for key in ("path", "digest", "size", "producer_run_id")
        },
    }
    state_path.with_name("observed-history.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )

    result = run_cli("mark-passes", cwd=root)

    assert result.returncode == 2
    assert "artifact lint observation is missing" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_active_gate_rejects_lint_observation_from_before_official_rehandoff(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    review = _write_review(root / "review.json")
    observed = run_cli(
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
        cwd=root,
    )
    assert observed.returncode == 0, observed.stderr

    state_path = state_dir / "sessions" / "test.json"
    historical = read_state(state_dir)
    historical.update(
        {
            "session_id": "observed-history",
            "mission_id": "observed-history",
            "phase": "done",
            "passes": True,
            "loop_active": False,
            "terminal_outcome": "completed_pass",
            "task_profile": {"primary": "portable-analysis"},
        }
    )
    state_path.with_name("observed-history.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )

    artifact_path = root / historical["artifact"]["path"]
    artifact_path.write_text("# Replacement\nverified output\n", encoding="utf-8")
    handoff = run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        "--artifact-applicability",
        "producing",
        "--artifact-path",
        historical["artifact"]["path"],
        "--producer-run-id",
        "executor-run-2",
        cwd=root,
    )
    assert handoff.returncode == 0, handoff.stderr
    current = read_state(state_dir)
    current["task_profile"] = {"primary": "portable-analysis"}
    current["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(current), encoding="utf-8")

    result = run_cli("mark-passes", cwd=root)

    assert result.returncode == 2
    assert "artifact lint observation" in result.stderr
    assert read_state(state_dir)["passes"] is False


@pytest.mark.parametrize(
    ("observed_count", "expected_gate_active", "expected_mark_returncode"),
    [(18, False, 0), (38, True, 2)],
)
def test_contradiction_remains_in_history_denominator_at_coverage_gate_boundary(
    observed_count,
    expected_gate_active,
    expected_mark_returncode,
    state_dir,
    run_cli,
    read_state,
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    state_path = state_dir / "sessions" / "test.json"
    current = read_state(state_dir)
    current["task_profile"] = {"primary": "portable-analysis"}
    current["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "open_high": 0}
    ]
    state_path.write_text(json.dumps(current), encoding="utf-8")

    terminal_common = {
        **current,
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
    }
    for index in range(observed_count):
        identity = {
            "path": f"reports/history-{index}.md",
            "digest": f"{index:064x}",
            "size": index,
            "producer_run_id": f"portable-history-{index}",
        }
        history = {
            **terminal_common,
            "session_id": f"history-{index}",
            "mission_id": f"history-{index}",
            "artifact": identity,
            "artifact_lint_status": "clean",
            "artifact_lint": [],
            "artifact_lint_identity": identity,
        }
        state_path.with_name(f"history-{index}.json").write_text(
            json.dumps(history), encoding="utf-8"
        )
    contradiction = {
        **terminal_common,
        "session_id": "contradiction",
        "mission_id": "contradiction",
        "passes": False,
        "terminal_outcome": "failed",
        "artifact_applicability": "not-applicable",
        "artifact": {"path": ["malformed"]},
        "artifact_lint_status": "clean",
    }
    state_path.with_name("contradiction.json").write_text(
        json.dumps(contradiction), encoding="utf-8"
    )
    forged_producing = {
        **terminal_common,
        "session_id": "forged-producing",
        "mission_id": "forged-producing",
        "passes": False,
        "terminal_outcome": "failed",
        "artifact_applicability": "producing",
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }
    state_path.with_name("forged-producing.json").write_text(
        json.dumps(forged_producing), encoding="utf-8"
    )

    stats_result = run_cli("stats", "--root", str(root), "--json", cwd=root)
    coverage = json.loads(stats_result.stdout)["artifact_coverage"]["by_profile"][
        "portable-analysis"
    ]
    result = run_cli("mark-passes", cwd=root)

    assert coverage["counts"]["eligible"] == observed_count + 2
    assert coverage["counts"]["observed"] == observed_count
    assert coverage["counts"]["invalid"] == 2
    assert coverage["coverage"] == pytest.approx(observed_count / (observed_count + 2))
    assert coverage["gate_active"] is expected_gate_active
    assert coverage["counts_conserved"] is True
    assert result.returncode == expected_mark_returncode, result.stderr
    assert read_state(state_dir)["passes"] is (not expected_gate_active)
