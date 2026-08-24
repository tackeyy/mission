"""Issue #501 K2 paired Snapshot and GuidanceFacts contract."""

from __future__ import annotations

import pytest

from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_bytes


def test_actual_cli_bytes_decode_to_one_provenance_bound_snapshot(tmp_path):
    from mission_kernel import decode_snapshot

    _path, source = generate_cli_state_bytes(tmp_path.resolve())

    snapshot = decode_snapshot(source)

    assert snapshot.state.snapshot_provenance == snapshot.provenance
    assert snapshot.guidance.provenance == snapshot.provenance
    assert snapshot.provenance.session_id == "test"
    assert snapshot.guidance.routing.complexity.value == "Standard"
    assert snapshot.guidance.planning.policy_version == 1


def test_unbound_or_recombined_pair_is_rejected_before_guidance(tmp_path):
    from mission_kernel import decode_mission_state, decode_snapshot
    from mission_kernel.guidance import GuidanceDerivationError, derive_next

    first_root = (tmp_path / "first").resolve()
    second_root = (tmp_path / "second").resolve()
    _first_path, first_source = generate_cli_state_bytes(first_root)
    _second_path, second_source = generate_cli_state_bytes(second_root, role="checker")
    first = decode_snapshot(first_source)
    second = decode_snapshot(second_source)

    with pytest.raises(GuidanceDerivationError) as unbound:
        derive_next(decode_mission_state(first_source), first.guidance)
    assert unbound.value.code == "snapshot-provenance-mismatch"

    with pytest.raises(GuidanceDerivationError) as recombined:
        derive_next(first.state, second.guidance)
    assert recombined.value.code == "snapshot-provenance-mismatch"


def test_bound_state_without_guidance_uses_the_typed_provenance_rejection(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import GuidanceDerivationError, derive_next

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    snapshot = decode_snapshot(source)

    with pytest.raises(GuidanceDerivationError) as rejected:
        derive_next(snapshot.state, None)

    assert rejected.value.code == "snapshot-provenance-mismatch"


@pytest.mark.parametrize(
    ("generation", "commit_digest"),
    [
        (-1, "sha256:" + "a" * 64),
        (1, None),
        (None, "sha256:" + "a" * 64),
    ],
    ids=["negative-generation", "missing-commit", "missing-generation"],
)
def test_snapshot_provenance_rejects_invalid_lineage(generation, commit_digest):
    from mission_kernel.model import SchemaOrigin, SnapshotProvenance

    with pytest.raises(ValueError) as rejected:
        SnapshotProvenance(
            schema_origin=SchemaOrigin.V5,
            session_id="test",
            document_digest="sha256:" + "b" * 64,
            generation=generation,
            commit_digest=commit_digest,
        )

    assert str(rejected.value) == "invalid-snapshot-provenance"


def test_snapshot_provenance_rejects_malformed_document_digest():
    from mission_kernel.model import SchemaOrigin, SnapshotProvenance

    with pytest.raises(ValueError) as rejected:
        SnapshotProvenance(
            schema_origin=SchemaOrigin.V5,
            session_id="test",
            document_digest="not-a-digest",
        )

    assert str(rejected.value) == "invalid-snapshot-provenance"


def test_v5_requires_the_closed_guidance_object():
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload.pop("guidance")
    with pytest.raises(MissionStateDecodeError) as rejected:
        from mission_kernel import decode_snapshot

        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == "missing-key"
    assert rejected.value.json_path == "$.guidance"


def test_v5_pregate_null_issue_ref_uses_typed_decode_rejection():
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"]["advisories"]["pregate"] = {
        "issue_ref": None,
        "subject_digest": "sha256:" + "d" * 64,
        "verdict": "accepted",
        "gate_id": "gate-501",
        "evaluated_at": "2026-08-15T00:00:00Z",
    }

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == "invalid-value"
    assert rejected.value.json_path == "$.guidance.advisories.pregate.issue_ref"


def _v5_payload_with_complete_nested_guidance_records():
    from .mission_state_fixture_corpus import current_v5_open_state

    payload = current_v5_open_state()
    invocation_id = "inv_" + "a" * 32
    selection_id = "sel_" + "b" * 32
    contract_digest = "sha256:" + "c" * 64
    payload["guidance"]["routing"]["issue_ref"] = "501"
    payload["guidance"]["advisories"]["pregate"] = {
        "issue_ref": "501",
        "subject_digest": "sha256:" + "d" * 64,
        "verdict": "accepted",
        "gate_id": "gate-501",
        "evaluated_at": "2026-08-15T00:00:00Z",
    }
    payload["guidance"]["providers"] = {
        "primary_binding": {
            "provider_id": "planner",
            "selection_id": selection_id,
            "planning_contract_digest": contract_digest,
        },
        "selections": [
            {
                "skill": "planning-provider",
                "provider_id": "planner",
                "selection_id": selection_id,
                "planning_mode": "primary",
                "planning_contract_digest": contract_digest,
                "required": False,
            }
        ],
        "invocations": [
            {
                "variant": "terminal",
                "invocation_id": invocation_id,
                "phase": "planning",
                "iteration": 1,
                "status": "completed",
                "lifecycle_state": "terminal",
                "required": False,
                "skill": "planning-provider",
                "provider_id": "planner",
                "selection_id": selection_id,
            }
        ],
        "imported_invocation_ids": [invocation_id],
    }
    return payload


_GUIDANCE_CLOSED_OBJECT_CASES = (
    (("guidance",), "schema", "$.guidance"),
    (("guidance", "routing"), "awaiting_user", "$.guidance.routing"),
    (("guidance", "planning"), "policy_version", "$.guidance.planning"),
    (("guidance", "review"), "tier", "$.guidance.review"),
    (("guidance", "advisories"), "pregate", "$.guidance.advisories"),
    (("guidance", "providers"), "selections", "$.guidance.providers"),
    (("guidance", "advisories", "pregate"), "gate_id", "$.guidance.advisories.pregate"),
    (("guidance", "providers", "primary_binding"), "provider_id", "$.guidance.providers.primary_binding"),
    (("guidance", "providers", "selections", 0), "skill", "$.guidance.providers.selections[0]"),
    (("guidance", "providers", "invocations", 0), "status", "$.guidance.providers.invocations[0]"),
)


@pytest.mark.parametrize(
    ("object_path", "field", "json_path"),
    _GUIDANCE_CLOSED_OBJECT_CASES,
    ids=[
        "guidance",
        "routing",
        "planning",
        "review",
        "advisories",
        "providers",
        "pregate",
        "primary-binding",
        "selection",
        "invocation",
    ],
)
@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_v5_guidance_nested_objects_have_exact_key_sets(
    object_path, field, json_path, mutation
):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes

    payload = _v5_payload_with_complete_nested_guidance_records()
    target = payload
    for part in object_path:
        target = target[part]
    if mutation == "missing":
        target.pop(field)
        expected_code = "missing-key"
    else:
        target["unexpected"] = True
        expected_code = "unknown-key"

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == expected_code
    assert rejected.value.json_path == (
        f"{json_path}.{field}" if mutation == "missing" else json_path
    )


@pytest.mark.parametrize(
    ("field_path", "value", "code", "json_path"),
    (
        (("guidance", "schema"), "future", "unknown-variant", "$.guidance.schema"),
        (("guidance", "routing", "complexity"), "Future", "unknown-variant", "$.guidance.routing.complexity"),
        (("guidance", "planning", "policy_version"), 2, "unknown-variant", "$.guidance.planning.policy_version"),
        (("guidance", "planning", "strategy"), "future", "unknown-variant", "$.guidance.planning.strategy"),
        (("guidance", "review", "tier"), "future", "unknown-variant", "$.guidance.review.tier"),
        (("guidance", "review", "tier_source"), "future", "unknown-variant", "$.guidance.review.tier_source"),
        (("guidance", "advisories", "pregate", "verdict"), "future", "unknown-variant", "$.guidance.advisories.pregate.verdict"),
        (("guidance", "providers", "selections", 0, "planning_mode"), "future", "unknown-variant", "$.guidance.providers.selections[0].planning_mode"),
        (("guidance", "providers", "invocations", 0, "variant"), "future", "unknown-variant", "$.guidance.providers.invocations[0].variant"),
        (("guidance", "providers", "invocations", 0, "phase"), "future", "unknown-variant", "$.guidance.providers.invocations[0].phase"),
        (("guidance", "providers", "invocations", 0, "lifecycle_state"), "running", "invariant-violation", "$.guidance.providers.invocations[0].lifecycle_state"),
        (("guidance", "providers", "selections", 0, "provider_id"), "x" * 129, "invalid-value", "$.guidance.providers.selections[0].provider_id"),
        (("guidance", "providers", "selections", 0, "skill"), "x" * 129, "invalid-value", "$.guidance.providers.selections[0].skill"),
        (("guidance", "providers", "invocations", 0, "skill"), "x" * 2049, "invalid-value", "$.guidance.providers.invocations[0].skill"),
        (("guidance", "providers", "invocations", 0, "iteration"), 1_000_001, "invalid-value", "$.guidance.providers.invocations[0].iteration"),
        (("guidance", "advisories", "pregate", "subject_digest"), "sha256:BAD", "invalid-value", "$.guidance.advisories.pregate.subject_digest"),
    ),
    ids=(
        "schema",
        "complexity",
        "policy-version",
        "strategy",
        "review-tier",
        "review-tier-source",
        "pregate-verdict",
        "planning-mode",
        "invocation-variant",
        "invocation-phase",
        "invocation-lifecycle",
        "provider-token-bound",
        "selection-skill-bound",
        "invocation-skill-bound",
        "iteration-bound",
        "digest",
    ),
)
def test_v5_guidance_enums_and_scalar_bounds_are_closed(
    field_path, value, code, json_path
):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    payload = _v5_payload_with_complete_nested_guidance_records()
    target = payload
    for part in field_path[:-1]:
        target = target[part]
    target[field_path[-1]] = value

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == code
    assert rejected.value.json_path == json_path


def test_absent_legacy_guidance_fields_use_the_documented_defaults(tmp_path):
    import json

    from mission_kernel import decode_snapshot

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    payload = json.loads(source)
    for field in (
        "awaiting_user",
        "complexity",
        "force_mission",
        "issue_ref",
        "planning_policy_version",
        "planning_provider_required",
        "planning_strategy",
        "critic_has_new_scope",
        "review_tier",
        "review_tier_source",
        "review_tier_signals",
        "pregate",
        "specialists_selected",
        "specialist_invocations",
        "provider_plan_imports",
        "planning_provider_binding",
    ):
        payload.pop(field, None)

    guidance = decode_snapshot(canonical_json_bytes(payload)).guidance

    assert guidance.routing.awaiting_user is False
    assert guidance.routing.complexity.value == "Unknown"
    assert guidance.routing.force_mission is False
    assert guidance.routing.issue_ref is None
    assert guidance.planning.policy_version is None
    assert guidance.planning.provider_required is False
    assert guidance.planning.strategy is None
    assert guidance.review.critic_has_new_scope is None
    assert guidance.review.tier == "standard"
    assert guidance.review.tier_source is None
    assert guidance.review.tier_signals == ()
    assert guidance.advisories.pregate is None
    assert guidance.providers.selections == ()
    assert guidance.providers.invocations == ()
    assert guidance.providers.imported_invocation_ids == ()
    assert guidance.providers.primary_binding is None


def test_v5_closed_guidance_decodes_with_the_same_non_wire_binding():
    from mission_kernel import decode_snapshot, encode_v5_snapshot

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"] = {
        "schema": "mission-guidance/1",
        "routing": {
            "awaiting_user": False,
            "complexity": "Complex",
            "force_mission": False,
            "issue_ref": "501",
        },
        "planning": {
            "policy_version": 1,
            "provider_required": False,
            "strategy": "core",
        },
        "review": {
            "critic_has_new_scope": True,
            "tier": "full",
            "tier_source": "auto",
            "tier_signals": ["security-keyword:credential"],
        },
        "advisories": {"pregate": None},
        "providers": {
            "primary_binding": None,
            "selections": [],
            "invocations": [],
            "imported_invocation_ids": [],
        },
    }

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert snapshot.state.snapshot_provenance == snapshot.guidance.provenance
    assert snapshot.guidance.routing.issue_ref == "501"
    assert snapshot.guidance.review.tier_signals == ("security-keyword:credential",)
    assert snapshot.provenance.generation is None
    assert snapshot.provenance.commit_digest is None
    assert encode_v5_snapshot(snapshot) == canonical_json_bytes(payload)


def test_v5_accepts_129_provider_selections_within_the_public_contract_limit():
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import MAX_GUIDANCE_SELECTIONS

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"]["providers"]["selections"] = [
        {
            "skill": f"fixture-{index}",
            "provider_id": None,
            "selection_id": f"sel_{index:032x}",
            "planning_mode": None,
            "planning_contract_digest": None,
            "required": False,
        }
        for index in range(129)
    ]

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert MAX_GUIDANCE_SELECTIONS == 1024
    assert len(snapshot.guidance.providers.selections) == 129


def test_v5_accepts_129_provider_invocations_within_the_public_contract_limit():
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import MAX_GUIDANCE_INVOCATIONS

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"]["providers"]["invocations"] = [
        {
            "variant": "terminal",
            "invocation_id": f"inv_{index:032x}",
            "phase": "review",
            "iteration": 2,
            "status": "failed",
            "lifecycle_state": "terminal",
            "required": False,
            "skill": f"fixture-{index}",
            "provider_id": None,
            "selection_id": None,
        }
        for index in range(129)
    ]

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert MAX_GUIDANCE_INVOCATIONS == 10_000
    assert len(snapshot.guidance.providers.invocations) == 129


def test_v5_accepts_129_plan_import_ids_bound_to_completed_invocations():
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import (
        MAX_GUIDANCE_INVOCATIONS,
        MAX_GUIDANCE_PLAN_IMPORTS,
    )

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    invocation_ids = [f"inv_{index:032x}" for index in range(129)]
    payload["guidance"]["providers"]["invocations"] = [
        {
            "variant": "terminal",
            "invocation_id": invocation_id,
            "phase": "planning",
            "iteration": 2,
            "status": "completed",
            "lifecycle_state": "terminal",
            "required": False,
            "skill": f"fixture-{index}",
            "provider_id": None,
            "selection_id": None,
        }
        for index, invocation_id in enumerate(invocation_ids)
    ]
    payload["guidance"]["providers"]["imported_invocation_ids"] = invocation_ids

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert MAX_GUIDANCE_PLAN_IMPORTS == MAX_GUIDANCE_INVOCATIONS == 10_000
    assert len(snapshot.guidance.providers.imported_invocation_ids) == 129


def test_v5_provider_and_pregate_variants_are_closed_and_round_trip():
    from mission_kernel import decode_snapshot, encode_v5_snapshot

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    selection_id = "sel_" + "b" * 32
    contract_digest = "sha256:" + "c" * 64
    payload["guidance"]["routing"]["issue_ref"] = "501"
    payload["guidance"]["advisories"]["pregate"] = {
        "issue_ref": "501",
        "subject_digest": "sha256:" + "d" * 64,
        "verdict": "split-required",
        "gate_id": "gate-501",
        "evaluated_at": "2026-08-15T00:00:00Z",
    }
    payload["guidance"]["planning"]["strategy"] = "provider-primary"
    payload["guidance"]["providers"] = {
        "primary_binding": {
            "provider_id": "planner",
            "selection_id": selection_id,
            "planning_contract_digest": contract_digest,
        },
        "selections": [
            {
                "skill": "planning-provider",
                "provider_id": "planner",
                "selection_id": selection_id,
                "planning_mode": "primary",
                "planning_contract_digest": contract_digest,
                "required": False,
            }
        ],
        "invocations": [
            {
                "variant": variant,
                "invocation_id": "inv_" + digit * 32,
                "phase": "planning",
                "iteration": 2,
                "status": status,
                "lifecycle_state": lifecycle,
                "required": False,
                "skill": "planning-provider",
                "provider_id": "planner",
                "selection_id": selection_id,
            }
            for variant, digit, status, lifecycle in (
                ("selected", "1", "selected", "selected"),
                ("reserved", "2", "reserved", "reserved"),
                ("running", "3", "running", "running"),
                ("terminal", "4", "completed", "terminal"),
            )
        ],
        "imported_invocation_ids": ["inv_" + "4" * 32],
    }

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert tuple(item.variant for item in snapshot.guidance.providers.invocations) == (
        "selected",
        "reserved",
        "running",
        "terminal",
    )
    assert snapshot.guidance.advisories.pregate.verdict == "split-required"
    from mission_kernel.guidance import derive_next

    recipe = derive_next(snapshot.state, snapshot.guidance)
    assert recipe.advisories == (
        "WARNING: pregate verdict=split-required。planning 前に分割を解決してください",
    )
    assert encode_v5_snapshot(snapshot) == canonical_json_bytes(payload)


def test_actual_cli_provider_projection_uses_closed_lineage_variants(tmp_path):
    from mission_kernel import decode_snapshot

    from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_corpus

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    snapshot = decode_snapshot(canonical_json_bytes(corpus["provider_plan"]))

    assert snapshot.guidance.planning.strategy == "provider-primary"
    assert snapshot.guidance.providers.primary_binding is not None
    assert len(snapshot.guidance.providers.selections) == 1
    assert len(snapshot.guidance.providers.invocations) == 1
    assert snapshot.guidance.providers.invocations[0].variant == "terminal"
    assert snapshot.guidance.providers.invocations[0].status == "completed"
    assert snapshot.guidance.providers.imported_invocation_ids == (
        snapshot.guidance.providers.invocations[0].invocation_id,
    )


def test_standalone_v5_state_decode_cannot_bypass_guidance_validation():
    from mission_kernel import decode_mission_state
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"]["routing"]["unexpected"] = True

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_mission_state(canonical_json_bytes(payload))

    assert rejected.value.code == "unknown-key"
    assert rejected.value.json_path == "$.guidance.routing"


@pytest.mark.parametrize(
    ("field", "value", "code", "path"),
    [
        (
            "issue_ref",
            "x" * 2049,
            "invalid-value",
            "$.guidance.routing.issue_ref",
        ),
        (
            "tier_signals",
            [f"signal-{index}" for index in range(129)],
            "collection-too-large",
            "$.guidance.review.tier_signals",
        ),
    ],
    ids=["text-bound", "collection-bound"],
)
def test_v5_guidance_limits_fail_closed(field, value, code, path):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    target = "routing" if field == "issue_ref" else "review"
    payload["guidance"][target][field] = value

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == code
    assert rejected.value.json_path == path


def test_malformed_present_legacy_guidance_field_does_not_use_absent_default(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_bytes

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    payload = __import__("json").loads(source)
    payload["awaiting_user"] = 1

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == "invalid-type"
    assert rejected.value.json_path == "$.awaiting_user"


@pytest.mark.parametrize(
    ("field", "value", "path"),
    [
        ("specialists_selected", False, "$.a4"),
        ("specialist_invocations", 0, "$.a4"),
        ("provider_plan_imports", "", "$.provider_plan_imports"),
        ("issue_ref", "x" * 4097, "$.issue_ref"),
        ("review_tier", "", "$.review_tier"),
        ("review_tier_signals", False, "$.review_tier_signals"),
    ],
    ids=[
        "selections",
        "invocations",
        "imports",
        "issue-ref-bound",
        "review-tier",
        "review-signals",
    ],
)
def test_present_malformed_legacy_guidance_collections_fail_closed(
    tmp_path, field, value, path
):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_bytes

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    payload = __import__("json").loads(source)
    payload[field] = value

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.json_path == path


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        ("bad-import-id", "$.guidance.providers.imported_invocation_ids[0]"),
        ("duplicate-selection", "$.guidance.providers.selections"),
        ("invalid-calendar-time", "$.guidance.advisories.pregate.evaluated_at"),
    ],
)
def test_v5_guidance_cross_field_identifiers_and_time_fail_closed(mutation, path):
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    selection_id = "sel_" + "a" * 32
    selection = {
        "skill": "planner",
        "provider_id": "provider",
        "selection_id": selection_id,
        "planning_mode": "advisory",
        "planning_contract_digest": "sha256:" + "b" * 64,
        "required": False,
    }
    if mutation == "bad-import-id":
        payload["guidance"]["providers"]["imported_invocation_ids"] = [
            "not-an-invocation"
        ]
    elif mutation == "duplicate-selection":
        payload["guidance"]["providers"]["selections"] = [selection, selection]
    else:
        payload["guidance"]["routing"]["issue_ref"] = "501"
        payload["guidance"]["advisories"]["pregate"] = {
            "issue_ref": "501",
            "subject_digest": "sha256:" + "c" * 64,
            "verdict": "accepted",
            "gate_id": "gate-501",
            "evaluated_at": "2026-99-99T99:99:99Z",
        }

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.json_path == path


def test_provider_plan_import_requires_a_current_completed_planning_invocation():
    from mission_kernel import decode_snapshot
    from mission_kernel.errors import MissionStateDecodeError

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["guidance"]["providers"]["imported_invocation_ids"] = [
        "inv_" + "a" * 32
    ]

    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_snapshot(canonical_json_bytes(payload))

    assert rejected.value.code == "invariant-violation"
    assert rejected.value.json_path == "$.guidance.providers.imported_invocation_ids[0]"


def test_provider_skill_names_follow_existing_portable_text_bounds():
    from mission_kernel import decode_snapshot, encode_v5_snapshot

    payload = _v5_payload_with_complete_nested_guidance_records()
    payload["guidance"]["providers"]["selections"][0]["skill"] = "計画担当"
    payload["guidance"]["providers"]["invocations"][0]["skill"] = "計画担当"
    source = canonical_json_bytes(payload)

    snapshot = decode_snapshot(source)

    assert snapshot.guidance.providers.selections[0].skill == "計画担当"
    assert snapshot.guidance.providers.invocations[0].skill == "計画担当"
    assert encode_v5_snapshot(snapshot) == source


def test_snapshot_rejects_identity_session_mismatch(tmp_path):
    from dataclasses import replace

    from mission_kernel import decode_snapshot
    from mission_kernel.snapshot import Snapshot

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    snapshot = decode_snapshot(source)
    forged_state = replace(
        snapshot.state,
        identity=replace(snapshot.state.identity, session_id="different-session"),
    )

    with pytest.raises(ValueError) as rejected:
        Snapshot(forged_state, snapshot.guidance, snapshot.provenance)

    assert str(rejected.value) == "snapshot-provenance-mismatch"


def test_equal_looking_provenance_from_separate_decodes_cannot_be_recombined(tmp_path):
    from dataclasses import replace

    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import GuidanceDerivationError, derive_next

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    first = decode_snapshot(source)
    second = decode_snapshot(source)
    assert first.provenance == second.provenance

    with pytest.raises(GuidanceDerivationError) as rejected:
        derive_next(first.state, second.guidance)

    assert rejected.value.code == "snapshot-provenance-mismatch"


def test_guidance_facts_has_no_public_constructor():
    from mission_kernel.guidance import GuidanceFacts

    with pytest.raises(TypeError):
        GuidanceFacts()


def test_transition_invalidates_read_snapshot_guidance_binding(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.guidance import GuidanceDerivationError, derive_next
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    snapshot = decode_snapshot(source)
    decision = decide(
        snapshot.state,
        MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "invalidate old read binding"),
    )
    assert decision.accepted is True
    assert decision.transition.new_state.snapshot_provenance is None

    with pytest.raises(GuidanceDerivationError) as rejected:
        derive_next(decision.transition.new_state, snapshot.guidance)

    assert rejected.value.code == "snapshot-provenance-mismatch"
