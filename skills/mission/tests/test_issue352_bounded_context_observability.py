"""#352: bounded context manifest generation and fallback observability."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _update_state(state_dir: Path, **updates) -> dict:
    path = state_dir / "sessions" / "test.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(updates)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _review(tmp_path: Path, iteration: int) -> Path:
    path = tmp_path / f"review-{iteration}.json"
    path.write_text(json.dumps({
        "schema": "mission-review/1",
        "perspective": "A",
        "iteration": iteration,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.5,
            "completeness": 4.5,
            "usability": 4.5,
        },
        "findings": [],
        "same_score_note": "all axes independently verified",
        "notes": "bounded context observation fixture",
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _manifest_record(
    path: Path,
    iteration: int,
    *,
    schema: str = "mission-context-manifest/1",
    raw: bytes | None = None,
) -> dict:
    if raw is None:
        raw = json.dumps({
            "schema": schema,
            "iteration": iteration,
            "mission_goal": "bounded test",
            "mission_id": "mission-test",
            "assumptions_path": ".mission-state/assumptions.md",
            "prior_findings": [],
        }, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "path": str(path),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "generated_at": "2026-08-07T00:00:00Z",
    }


def _aggregate(run_cli, state_dir: Path, tmp_path: Path, iteration: int):
    out = tmp_path / f"score-{iteration}.json"
    result = run_cli(
        "aggregate-reviews",
        "--iteration", str(iteration),
        "--input", str(_review(tmp_path, iteration)),
        "--out", str(out),
        "--json",
        cwd=state_dir.parent,
    )
    return result, out


def test_generated_manifest_is_recorded_and_aggregate_observes_bounded(
    state_dir, run_cli, tmp_path,
):
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={
            "1": {
                "path": "previous.json",
                "digest": "sha256:previous",
                "generated_at": "2026-08-06T00:00:00Z",
            }
        },
    )
    # #711: evidence is published as a projection of the repository, so it
    # cannot land inside the repository's own subtree.
    manifest_path = state_dir.parent / "context-manifest-iter2.json"

    generated = run_cli(
        "context-manifest",
        "--iteration", "2",
        "--out", str(manifest_path),
        cwd=state_dir.parent,
    )

    assert generated.returncode == 0, generated.stderr
    state = json.loads((state_dir / "sessions" / "test.json").read_text(encoding="utf-8"))
    observation = state["context_manifests"]["2"]
    expected_digest = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    # #711: the recorded path is the canonical project-relative form, not the
    # string the caller typed.  A projection target is relative to the project,
    # and the record has to name the same thing the generation holds.
    assert observation["path"] == "context-manifest-iter2.json"
    assert observation["digest"] == expected_digest
    assert observation["generated_at"].endswith("Z")
    assert state["context_manifests"]["1"]["path"] == "previous.json"

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_mode_expected"] == "bounded"
    assert evidence["context_manifest_generated"] is True


@pytest.mark.parametrize("iteration", [0, -1])
def test_context_manifest_rejects_invalid_iteration_without_changing_state(
    state_dir, run_cli, tmp_path, iteration,
):
    before = json.loads(
        (state_dir / "sessions" / "test.json").read_text(encoding="utf-8")
    )

    result = run_cli(
        "context-manifest",
        "--iteration", str(iteration),
        "--out", str(tmp_path / "invalid-manifest.json"),
        cwd=state_dir.parent,
    )

    after = json.loads(
        (state_dir / "sessions" / "test.json").read_text(encoding="utf-8")
    )
    assert result.returncode == 2
    assert "--iteration は 1 以上" in result.stderr
    assert after == before
    assert not (tmp_path / "invalid-manifest.json").exists()


def test_missing_expected_manifest_warns_without_changing_aggregate_gate(
    state_dir, run_cli, tmp_path,
):
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
    )

    aggregated, out = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    assert out.exists()
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_mode_expected"] == "bounded"
    assert evidence["context_manifest_generated"] is False


def test_malformed_manifest_record_is_treated_as_missing(
    state_dir, run_cli, tmp_path,
):
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={"2": {}},
    )

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_manifest_generated"] is False


@pytest.mark.parametrize(
    "failure",
    ["missing-file", "bad-digest", "bad-time", "invalid-json", "bad-schema", "wrong-iteration"],
)
def test_unverifiable_manifest_is_treated_as_missing(
    state_dir, run_cli, tmp_path, failure,
):
    manifest_path = tmp_path / f"manifest-{failure}.json"
    if failure == "missing-file":
        record = {
            "path": str(manifest_path),
            "digest": "sha256:" + "0" * 64,
            "generated_at": "2026-08-07T00:00:00Z",
        }
    elif failure == "invalid-json":
        record = _manifest_record(manifest_path, 2, raw=b"not-json")
    elif failure == "bad-schema":
        record = _manifest_record(manifest_path, 2, schema="wrong/1")
    elif failure == "wrong-iteration":
        record = _manifest_record(manifest_path, 3)
    else:
        record = _manifest_record(manifest_path, 2)
        if failure == "bad-digest":
            record["digest"] = "sha256:" + "0" * 64
        else:
            record["generated_at"] = "not-an-iso-timestamp"
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={"2": record},
    )

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_manifest_generated"] is False


@pytest.mark.parametrize(
    "payload_iteration",
    [0, True, 2.0, -1],
    ids=["zero", "bool", "float", "negative"],
)
def test_aggregate_rejects_noncanonical_manifest_iteration(
    state_dir, run_cli, tmp_path, payload_iteration,
):
    manifest_path = tmp_path / "manifest-invalid-iteration.json"
    record = _manifest_record(manifest_path, payload_iteration)
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={"2": record},
    )

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_manifest_generated"] is False


@pytest.mark.parametrize(
    "generated_at",
    ["2026-08-07", "2026-08-07T00:00:00"],
    ids=["date-only", "timezone-naive"],
)
def test_aggregate_rejects_manifest_without_timezone_aware_generation_time(
    state_dir, run_cli, tmp_path, generated_at,
):
    manifest_path = tmp_path / "manifest-invalid-time.json"
    record = _manifest_record(manifest_path, 2)
    record["generated_at"] = generated_at
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={"2": record},
    )

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_manifest_generated"] is False


def test_aggregate_tampered_nul_path_warns_without_partial_state_corruption(
    state_dir, run_cli, tmp_path,
):
    manifest_path = tmp_path / "manifest-before-path-tamper.json"
    record = _manifest_record(manifest_path, 2)
    record["path"] = "manifest\x00tampered.json"
    _update_state(
        state_dir,
        iteration=2,
        phase="reviewing",
        critic_has_new_scope=False,
        context_manifests={"2": record},
    )

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 2)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352: bounded context expected but no manifest generated" in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_manifest_generated"] is False
    persisted = json.loads(
        (state_dir / "sessions" / "test.json").read_text(encoding="utf-8")
    )
    assert persisted["context_manifests"]["2"] == record
    assert persisted["last_parallel_execution"] == "unknown"


def test_iteration_one_records_full_expectation_without_warning(
    state_dir, run_cli, tmp_path,
):
    _update_state(state_dir, iteration=1, phase="reviewing")

    aggregated, _ = _aggregate(run_cli, state_dir, tmp_path, 1)

    assert aggregated.returncode == 0, aggregated.stderr
    assert "WARN #352" not in aggregated.stderr
    result = json.loads(aggregated.stdout)
    evidence = json.loads(Path(result["findings_evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["context_mode_expected"] == "full"
    assert evidence["context_manifest_generated"] is False


def test_stats_json_counts_bounded_generation_and_full_fallback(run_cli, tmp_path):
    states = [
        ("generated", 2, False, True),
        ("fallback", 2, False, False),
        ("malformed", 2, False, False),
        ("full", 1, None, False),
    ]
    for name, iteration, critic_scope, has_manifest in states:
        project = tmp_path / name
        session_dir = project / ".mission-state" / "sessions"
        session_dir.mkdir(parents=True)
        manifests = {}
        if has_manifest:
            manifests[str(iteration)] = _manifest_record(
                project / "manifest.json", iteration,
            )
            manifests[str(iteration)]["path"] = "manifest.json"
        elif name == "malformed":
            manifests[str(iteration)] = {}
        state = {
            "mission": f"mission {name}",
            "mission_id": f"mission-{name}",
            "session_id": f"session-{name}",
            "project_root": str(project),
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "iteration": iteration,
            "score_history": [],
            "context_manifests": manifests,
        }
        if critic_scope is not None:
            state["critic_has_new_scope"] = critic_scope
        (session_dir / f"{name}.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    stats = json.loads(result.stdout)
    assert stats["bounded_context_counts"] == {
        "expected_bounded": 3,
        "manifest_generated": 1,
        "fallback_full": 2,
    }


def test_stats_rejects_noncanonical_expected_and_payload_iterations(run_cli, tmp_path):
    for index, iteration in enumerate((0, True, 2.0, -1)):
        project = tmp_path / f"invalid-{index}"
        session_dir = project / ".mission-state" / "sessions"
        session_dir.mkdir(parents=True)
        record = _manifest_record(project / "manifest.json", iteration)
        state = {
            "mission": f"invalid iteration {iteration!r}",
            "mission_id": f"mission-invalid-{index}",
            "session_id": f"session-invalid-{index}",
            "project_root": str(project),
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "iteration": iteration,
            "score_history": [],
            "critic_has_new_scope": False,
            "context_manifests": {str(iteration): record},
        }
        (session_dir / f"invalid-{index}.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["bounded_context_counts"] == {
        "expected_bounded": 0,
        "manifest_generated": 0,
        "fallback_full": 0,
    }


def test_stats_continues_when_manifest_path_contains_nul(run_cli, tmp_path):
    project = tmp_path / "tampered-path"
    session_dir = project / ".mission-state" / "sessions"
    session_dir.mkdir(parents=True)
    record = _manifest_record(project / "manifest.json", 2)
    record["path"] = "manifest\x00tampered.json"
    state = {
        "mission": "tampered manifest path",
        "mission_id": "mission-tampered-path",
        "session_id": "session-tampered-path",
        "project_root": str(project),
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "iteration": 2,
        "score_history": [],
        "critic_has_new_scope": False,
        "context_manifests": {"2": record},
    }
    (session_dir / "tampered-path.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["bounded_context_counts"] == {
        "expected_bounded": 1,
        "manifest_generated": 0,
        "fallback_full": 1,
    }


def test_reviewer_and_changelogs_document_bounded_context_observation_contract():
    reviewer = (REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    assert "context: bounded" in reviewer

    for relative in (
        "CHANGELOG.md",
        "CHANGELOG.ja.md",
        "plugins/mission/CHANGELOG.md",
        "plugins/mission/CHANGELOG.ja.md",
    ):
        assert "#352" in (REPO_ROOT / relative).read_text(encoding="utf-8")
