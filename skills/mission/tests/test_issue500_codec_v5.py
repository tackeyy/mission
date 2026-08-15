"""Issue #500: v5 MissionState closed codec and production reachability gates."""

from __future__ import annotations

import ast
import copy
import importlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state


def _decode(payload: dict):
    from mission_kernel import decode_mission_state

    return decode_mission_state(canonical_json_bytes(payload))


def _decode_snapshot_payload(payload: dict):
    from mission_kernel import decode_snapshot

    return decode_snapshot(canonical_json_bytes(payload))


def _assert_rejected(payload: dict, code: str, path: str):
    from mission_kernel.errors import MissionStateDecodeError

    source = canonical_json_bytes(payload)
    before = source[:]
    with pytest.raises(MissionStateDecodeError) as rejected:
        _decode(payload)
    assert rejected.value.code == code
    assert rejected.value.json_path == path
    assert source == before


def _nested(payload: dict, path: tuple[object, ...]):
    current = payload
    for part in path:
        current = current[part]
    return current


def test_package_root_does_not_export_v5_encoder_or_private_decoders():
    import mission_kernel
    from mission_kernel import codec_v4, codec_v5

    assert "encode_v5_state" not in getattr(mission_kernel, "__all__", ())
    assert not hasattr(mission_kernel, "encode_v5_state")
    assert not hasattr(codec_v4, "decode_v4_state")
    assert not hasattr(codec_v5, "decode_v5_state")


@pytest.mark.parametrize("package_root", ["skills", "plugins"], ids=["canonical", "plugin"])
def test_fresh_interpreter_cannot_import_v5_encoder_from_package_root(package_root):
    repository = Path(__file__).resolve().parents[3]
    if package_root == "skills":
        library = repository / "skills" / "mission" / "lib"
    else:
        library = repository / "plugins" / "mission" / "skills" / "mission" / "lib"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(library)
    result = subprocess.run(
        [sys.executable, "-c", "from mission_kernel import encode_v5_state"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert "ImportError" in result.stderr


def test_projection_helper_preserves_exact_projection_key_set():
    from mission_kernel.codec_v5 import _project_terminal_outcome_control

    projected = _project_terminal_outcome_control(current_v5_open_state()["control"])

    assert projected == {
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "session_role": "implementer",
    }


@pytest.mark.parametrize(
    "missing",
    [
        "identity",
        "control",
        "plan",
        "handoff",
        "reviews",
        "findings",
        "scores",
        "lease",
        "extensions",
    ],
)
def test_v5_requires_every_top_level_key(missing):
    payload = current_v5_open_state()
    payload.pop(missing)
    _assert_rejected(payload, "missing-key", f"$.{missing}")


def test_v5_private_decoder_requires_schema_version_key():
    from mission_kernel.codec_v5 import _decode_v5_state
    from mission_kernel.errors import MissionStateDecodeError

    payload = current_v5_open_state()
    payload.pop("schema_version")
    with pytest.raises(MissionStateDecodeError) as rejected:
        _decode_v5_state(canonical_json_bytes(payload))
    assert rejected.value.code == "missing-key"
    assert rejected.value.json_path == "$.schema_version"


@pytest.mark.parametrize("missing", ["mission", "mission_id", "session_id"])
def test_v5_identity_requires_every_key(missing):
    payload = current_v5_open_state()
    payload["identity"].pop(missing)
    _assert_rejected(payload, "missing-key", f"$.identity.{missing}")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mission", " "),
        ("mission_id", ""),
        ("session_id", "x" * 129),
    ],
    ids=["mission-trim", "mission-id-empty", "session-id-too-long"],
)
def test_v5_identity_enforces_trim_nonempty_and_lengths(field, value):
    payload = current_v5_open_state()
    payload["identity"][field] = value
    _assert_rejected(payload, "invalid-value", f"$.identity.{field}")


@pytest.mark.parametrize("missing", list(current_v5_open_state()["control"]))
def test_v5_control_requires_exact_fields(missing):
    payload = current_v5_open_state()
    payload["control"].pop(missing)
    _assert_rejected(payload, "missing-key", f"$.control.{missing}")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("iteration", True, "invalid-type"),
        ("iteration", -1, "invalid-value"),
        ("max_iter", 0, "invalid-value"),
        ("threshold", 6, "invalid-value"),
        ("threshold", -0.1, "invalid-value"),
        ("reviewer_count", 0, "invalid-value"),
        ("stagnation_count", -1, "invalid-value"),
        ("loop_active", 1, "invalid-type"),
        ("passes", 0, "invalid-type"),
    ],
)
def test_v5_control_rejects_invalid_scalars(field, value, code):
    payload = current_v5_open_state()
    payload["control"][field] = value
    _assert_rejected(payload, code, f"$.control.{field}")


@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ((), "top_extra"),
        (("identity",), "identity_extra"),
        (("control",), "control_extra"),
        (("plan",), "plan_extra"),
        (("handoff",), "handoff_extra"),
        (("handoff", "plan"), "handoff_plan_extra"),
        (("reviews", 0), "review_extra"),
        (("lease",), "lease_extra"),
    ],
    ids=[
        "top",
        "identity",
        "control",
        "plan",
        "handoff",
        "handoff-plan",
        "review",
        "lease",
    ],
)
def test_v5_closed_objects_reject_unknown_keys(path, extra):
    payload = current_v5_open_state()
    _nested(payload, path)[extra] = True
    json_path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path)
    _assert_rejected(payload, "unknown-key", json_path)


@pytest.mark.parametrize(
    ("path", "value", "json_path"),
    [
        (("control", "phase"), "execution", "$.control.phase"),
        (("control", "terminal_outcome"), "future", "$.control.terminal_outcome"),
        (("plan", "kind"), "future", "$.plan.kind"),
        (("plan", "source"), "future", "$.plan.source"),
        (("handoff", "kind"), "future", "$.handoff.kind"),
        (("reviews", 0, "kind"), "future", "$.reviews[0].kind"),
        (("lease", "kind"), "legacy-absent", "$.lease.kind"),
    ],
)
def test_v5_closed_unions_reject_unknown_variants(path, value, json_path):
    payload = current_v5_open_state()
    parent = _nested(payload, path[:-1])
    parent[path[-1]] = value
    _assert_rejected(payload, "unknown-variant", json_path)


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("plan", "digest"), "sha256:not-a-digest", "invalid-value"),
        (("plan", "source_digest"), "sha256:not-a-digest", "invalid-value"),
        (("plan", "validated_at"), "2026-08-15T00:00:00+00:00", "invalid-value"),
        (("handoff", "plan", "digest"), "sha256:" + "9" * 64, "invariant-violation"),
    ],
)
def test_v5_plan_and_handoff_validate_digest_time_and_binding(path, value, code):
    payload = current_v5_open_state()
    parent = _nested(payload, path[:-1])
    parent[path[-1]] = value
    _assert_rejected(payload, code, "$" + "".join(f".{part}" for part in path))


def _open_finding() -> dict:
    return {
        "id": "finding-1",
        "generation": 3,
        "iteration": 2,
        "reviewer": "quality",
        "severity": "Medium",
        "axis": "accuracy",
        "summary": "bounded finding",
        "recommendation": "fix it",
        "evidence_ref": {
            "kind": "review-input",
            "relative_path": ".mission-state/archive/finding.json",
            "digest": "sha256:" + "4" * 64,
            "size": 100,
            "iteration": 2,
            "perspective": "quality",
        },
        "status": "open",
    }


def _resolved_finding() -> dict:
    finding = _open_finding()
    finding.update(
        {
            "status": "resolved",
            "prior_identity": {"id": "finding-1", "generation": 2},
            "resolution_evidence_ref": {
                "kind": "finding-resolution",
                "relative_path": ".mission-state/archive/finding-resolution.json",
                "digest": "sha256:" + "5" * 64,
                "size": 120,
            },
            "resolved_at": "2026-08-15T00:00:00Z",
        }
    )
    return finding


@pytest.mark.parametrize("status", ["accepted-risk", "not-reproducible", "future"])
def test_v5_rejects_non_authoritative_finding_statuses(status):
    payload = current_v5_open_state()
    finding = _open_finding()
    finding["status"] = status
    payload["findings"] = [finding]
    _assert_rejected(payload, "finding-status-invalid", "$.findings[0].status")


@pytest.mark.parametrize("missing", ["prior_identity", "resolution_evidence_ref", "resolved_at"])
def test_v5_resolved_finding_requires_all_resolution_fields(missing):
    payload = current_v5_open_state()
    finding = _resolved_finding()
    finding.pop(missing)
    payload["findings"] = [finding]
    _assert_rejected(payload, "missing-key", f"$.findings[0].{missing}")


@pytest.mark.parametrize("resolution_field", ["prior_identity", "resolution_evidence_ref", "resolved_at"])
def test_v5_open_finding_rejects_every_resolution_field(resolution_field):
    payload = current_v5_open_state()
    finding = _open_finding()
    finding[resolution_field] = _resolved_finding()[resolution_field]
    payload["findings"] = [finding]
    _assert_rejected(payload, "unknown-key", "$.findings[0]")


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("prior_identity", "id"), "other", "invariant-violation"),
        (("prior_identity", "generation"), 3, "invariant-violation"),
        (("resolution_evidence_ref", "kind"), "review-input", "unknown-variant"),
        (("resolved_at",), "2026-08-15T00:00:00+00:00", "invalid-value"),
    ],
)
def test_v5_resolved_finding_rejects_invalid_resolution_authority(path, value, code):
    payload = current_v5_open_state()
    finding = _resolved_finding()
    parent = _nested(finding, path[:-1])
    parent[path[-1]] = value
    payload["findings"] = [finding]
    json_path = "$.findings[0]" + "".join(f".{part}" for part in path)
    _assert_rejected(payload, code, json_path)


def test_v5_resolved_document_round_trips_canonically():
    from mission_kernel import encode_v5_snapshot
    from mission_kernel.model import ResolvedFinding

    payload = current_v5_open_state()
    payload["findings"] = [_resolved_finding()]
    snapshot = _decode_snapshot_payload(payload)
    decoded = snapshot.state
    encoded = encode_v5_snapshot(snapshot)

    assert isinstance(decoded.findings.findings[0], ResolvedFinding)
    assert encoded == canonical_json_bytes(payload)
    assert _decode(json.loads(encoded)) == replace(decoded, snapshot_provenance=None)


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("lease", "lease_expires_at"), "not-a-time", "invalid-value"),
        (("lease", "fencing_epoch"), True, "invalid-type"),
    ],
)
def test_v5_lease_rejects_invalid_time_and_epoch(path, value, code):
    payload = current_v5_open_state()
    parent = _nested(payload, path[:-1])
    parent[path[-1]] = value
    _assert_rejected(payload, code, "$" + "".join(f".{part}" for part in path))


@pytest.mark.parametrize(
    ("epochs", "lease_ids"),
    [([1, 1], ["retired-a", "retired-b"]), ([1, 2], ["retired-a", "retired-a"]), ([1, 2], ["retired-a", "lease-500"])],
    ids=["non-increasing-epoch", "duplicate-history-token", "current-token-in-history"],
)
def test_v5_lease_rejects_history_contradictions(epochs, lease_ids):
    payload = current_v5_open_state()
    payload["lease"]["fencing_epoch"] = 3
    payload["lease"]["lease_history"] = [
        {
            "owner_session_id": "session-old",
            "lease_id": lease_id,
            "fencing_epoch": epoch,
            "reason": "takeover",
            "at": f"2026-08-15T00:00:0{index}Z",
        }
        for index, (epoch, lease_id) in enumerate(zip(epochs, lease_ids))
    ]
    _assert_rejected(payload, "invariant-violation", "$.lease.lease_history")


def test_v5_rejects_legacy_score_variant():
    payload = current_v5_open_state()
    payload["scores"] = [{"source": "legacy-unverified", "composite": 4.5}]
    _assert_rejected(payload, "unknown-variant", "$.scores[0].source")


def _bound_score_document(source="scoring-json"):
    source_key = "manual_evidence_ref" if source == "manual-import" else "review_evidence_ref"
    source_reference = {
        "kind": "manual-score" if source == "manual-import" else "review-aggregate",
        "relative_path": ".mission-state/archive/source-evidence.json",
        "digest": "sha256:" + "6" * 64,
        "size": 240,
        "generation": "aggregate-generation",
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
    }
    if source == "scoring-json":
        source_reference["iteration"] = 2
    return {
        "source": source,
        "items": {
            "mission_achievement": 4.5,
            "accuracy": 4.4,
            "completeness": 4.3,
            "usability": 4.2,
        },
        "composite": 4.35,
        "min_item": 4.2,
        "agreement": 4.8,
        "open_high": 0,
        source_key: source_reference,
        "scoring_evidence_ref": {
            "kind": "scoring-artifact",
            "relative_path": ".mission-state/archive/scoring.json",
            "digest": "sha256:" + "7" * 64,
            "size": 180,
        },
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
    }


def test_v5_rejects_unknown_keys_and_legacy_paths_in_nested_score_objects():
    from mission_kernel.errors import MissionStateDecodeError

    cases = []
    for path in (
        ("review_evidence_ref", "revision_scope"),
        ("scoring_evidence_ref",),
        ("revision_scope",),
    ):
        payload = current_v5_open_state()
        payload["scores"] = [_bound_score_document()]
        _nested(payload["scores"][0], path)["unknown"] = True
        cases.append(payload)
    legacy_path = current_v5_open_state()
    legacy_path["scores"] = [_bound_score_document()]
    reference = legacy_path["scores"][0]["scoring_evidence_ref"]
    reference["path"] = reference.pop("relative_path")
    for payload in cases:
        with pytest.raises(MissionStateDecodeError) as rejected:
            _decode(payload)
        assert rejected.value.code == "unknown-key"
    with pytest.raises(MissionStateDecodeError) as rejected:
        _decode(legacy_path)
    assert rejected.value.code == "missing-key"
    assert rejected.value.json_path == "$.scores[0].scoring_evidence_ref.relative_path"


def test_v5_rejects_unknown_aggregate_finding_evidence_and_null_partial_lineage():
    aggregate = _bound_score_document()["review_evidence_ref"]
    aggregate["unknown"] = True
    finding_payload = current_v5_open_state()
    finding = _open_finding()
    finding["evidence_ref"] = aggregate
    finding_payload["findings"] = [finding]
    _assert_rejected(finding_payload, "unknown-key", "$.findings[0].evidence_ref")

    lineage_payload = current_v5_open_state()
    lineage_payload["scores"] = [_bound_score_document()]
    lineage_payload["scores"][0]["review_evidence_ref"]["review_group_id"] = None
    _assert_rejected(
        lineage_payload,
        "partial-lineage",
        "$.scores[0].review_evidence_ref",
    )


def test_v5_lease_history_is_closed_and_requires_canonical_timestamp():
    entry = {
        "owner_session_id": "retired-session",
        "lease_id": "retired-lease",
        "fencing_epoch": 1,
        "reason": "takeover",
        "at": "2026-08-15T00:00:00Z",
    }
    unknown_payload = current_v5_open_state()
    unknown_payload["lease"]["fencing_epoch"] = 2
    unknown_payload["lease"]["lease_history"] = [{**entry, "unknown": True}]
    _assert_rejected(unknown_payload, "unknown-key", "$.lease.lease_history[0]")

    time_payload = current_v5_open_state()
    time_payload["lease"]["fencing_epoch"] = 2
    time_payload["lease"]["lease_history"] = [{**entry, "at": "2026-08-15T00:00:00+00:00"}]
    _assert_rejected(time_payload, "invalid-value", "$.lease.lease_history[0].at")


@pytest.mark.parametrize("source", ["scoring-json", "manual-import"])
def test_v5_bound_score_variants_round_trip_with_complete_provenance(source):
    from mission_kernel import encode_v5_snapshot
    from mission_kernel.model import BoundScore

    payload = current_v5_open_state()
    payload["scores"] = [_bound_score_document(source)]

    snapshot = _decode_snapshot_payload(payload)
    decoded = snapshot.state
    assert isinstance(decoded.scores[0], BoundScore)
    assert decoded.scores[0].source.value == source
    if source == "manual-import":
        assert decoded.scores[0].manual_evidence_ref.kind == "manual-score"
        assert decoded.scores[0].review_evidence_ref is None
    assert encode_v5_snapshot(snapshot) == canonical_json_bytes(payload)


def test_encode_v5_revalidates_hand_built_model_invariants():
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel.errors import MissionStateDecodeError

    snapshot = _decode_snapshot_payload(current_v5_open_state())
    invalid = replace(snapshot.state, control=replace(snapshot.state.control, threshold=6.0))
    with pytest.raises(MissionStateDecodeError) as rejected:
        encode_v5_state(invalid, snapshot.guidance)
    assert rejected.value.code == "invalid-value"
    assert rejected.value.json_path == "$.control.threshold"


def test_production_entrypoint_ast_graph_and_parser_have_no_v5_producer_route():
    repository = Path(__file__).resolve().parents[3]
    forbidden_imports = {"mission_kernel.codec_v5", "encode_v5_state", "ResolvedFinding"}
    forbidden_commands = {"resolve-finding", "migrate-v5", "init-v5"}
    state_script = repository / "skills" / "mission" / "bin" / "mission-state.py"

    for base in (repository / "skills" / "mission" / "bin", repository / "scripts"):
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
                    imported.update(alias.name for alias in node.names)
            assert forbidden_imports.isdisjoint(imported), path

    spec = importlib.util.spec_from_file_location("issue500_parser_inventory", state_script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    parser = module._build_parser()
    root_subparsers = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
    assert forbidden_commands.isdisjoint(root_subparsers.choices)


def test_migration_execute_preserves_legacy_schema_and_never_emits_v5(tmp_path):
    repository = Path(__file__).resolve().parents[3]
    migrate = repository / "skills" / "mission" / "bin" / "mission-migrate.py"
    project = tmp_path / "project"
    state_dir = project / ".mission-state"
    state_dir.mkdir(parents=True)
    legacy = {
        "schema_version": 4,
        "mission": "migration fixture",
        "mission_id": "migration-fixture",
        "session_id": "legacy",
        "loop_active": False,
        "passes": False,
        "halt_reason": "fixture halt",
    }
    (state_dir / "state.json").write_bytes(canonical_json_bytes(legacy))

    result = subprocess.run(
        [sys.executable, str(migrate), str(project), "--execute"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    migrated = json.loads((state_dir / "sessions" / "legacy.json").read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 4
    assert '"schema_version": 5' not in result.stdout
