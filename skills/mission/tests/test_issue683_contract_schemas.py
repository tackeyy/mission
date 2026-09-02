"""Issue #683: the input contracts are discoverable without hitting errors.

`planning adopt-core` and `review-import` both validate imperatively, so the
only way to learn what they require was to submit a document and read the
rejection -- seven times for the plan contract, once per field for reviews.

Publishing a schema creates a second place the same rules live, which is the
failure this issue must not introduce.  These tests bind the published schema
to the validator: every field the schema calls required must actually be
rejected when removed, and every error code the schema names must be one the
validator can raise.  A schema that drifts from the implementation fails here.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load_mission_state():
    path = ROOT / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_683", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load_mission_state()


def _plan_document():
    return {
        "objective": "bounded core plan",
        "scope": {
            "resources": [],
            "actions": [{"type": "analyze", "effect_class": "reversible"}],
        },
        "assumptions": [
            {"id": "a-1", "statement": "input is available", "validation": "inspect input"}
        ],
        "steps": [
            {
                "id": "inspect",
                "action": "analyze",
                "inputs": [],
                "outputs": ["findings"],
                "depends_on": [],
                "acceptance_checks": ["findings are recorded"],
                "risk": "low",
                "rollback": "none",
            }
        ],
        "global_acceptance": ["all steps complete"],
        "stop_conditions": ["required input is unavailable"],
    }


def _review_payload():
    return {
        "schema": "mission-review/1",
        "perspective": "A",
        "iteration": 1,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.4,
            "completeness": 4.3,
            "usability": 4.2,
        },
        "findings": [
            {
                "id": "A-1",
                "severity": "Medium",
                "axis": "accuracy",
                "evidence": "lib/x.py:10 -- quoted",
            }
        ],
        "same_score_note": None,
    }


def test_the_plan_schema_is_published_and_readable():
    from plan_contract import contract_schema

    schema = contract_schema()

    assert schema["schema"] == "mission-contract-schema/1"
    assert schema["contract"] == "planning-adopt-core"
    assert schema["required"]
    assert schema["enums"]


def test_the_review_schema_is_published_and_readable():
    schema = MS.review_contract_schema()

    assert schema["schema"] == "mission-contract-schema/1"
    assert schema["contract"] == "review-import"
    assert schema["required"]
    assert schema["enums"]


@pytest.mark.parametrize("field", sorted(_plan_document()))
def test_every_documented_plan_field_is_actually_required(field, tmp_path):
    """Removing a documented required field must be rejected by the validator."""
    from plan_contract import PlanContractError, contract_schema, validate_plan_document

    assert field in contract_schema()["required"], (
        f"{field} is enforced but the schema does not list it"
    )
    document = _plan_document()
    document.pop(field)

    with pytest.raises(PlanContractError):
        validate_plan_document(document, tmp_path)


def test_the_plan_schema_lists_no_field_the_validator_ignores(tmp_path):
    """A schema may not claim a requirement the implementation does not hold."""
    from plan_contract import PlanContractError, contract_schema, validate_plan_document

    for field in contract_schema()["required"]:
        document = _plan_document()
        document.pop(field, None)
        with pytest.raises(PlanContractError):
            validate_plan_document(document, tmp_path)


def test_no_field_the_plan_validator_requires_is_missing_from_the_schema(tmp_path):
    """The other direction, so the schema cannot fall behind the validator.

    Removing fields one at a time can only reach what the fixture already has,
    so a brand-new requirement would never be probed.  Asserting the fixture is
    still accepted is what closes that: a new required field makes the fixture
    invalid, which fails here and forces the author through the loop that also
    publishes it.
    """
    from plan_contract import PlanContractError, contract_schema, validate_plan_document

    validate_plan_document(_plan_document(), tmp_path)

    published = set(contract_schema()["required"])
    for field in _plan_document():
        document = _plan_document()
        document.pop(field)
        try:
            validate_plan_document(document, tmp_path)
        except PlanContractError:
            enforced = True
        else:
            enforced = False
        if enforced:
            assert field in published, f"{field} is enforced but not published"


@pytest.mark.parametrize(
    "field", ["schema", "iteration", "perspective", "scores", "findings"]
)
def test_every_documented_review_field_is_actually_required(field):
    """Same binding for the review contract."""
    assert field in MS.review_contract_schema()["required"]
    payload = _review_payload()
    payload.pop(field)

    with pytest.raises(ValueError):
        MS._validate_review_payload(payload, 1)


def test_no_field_the_review_validator_requires_is_missing_from_the_schema():
    """The other direction: a validator requirement absent from the schema.

    Checking only that documented fields are enforced lets the schema fall
    behind silently -- a new requirement added to the validator would never be
    published, which is the drift this PR exists to prevent.
    """
    MS._validate_review_payload(_review_payload(), 1)

    published = set(MS.review_contract_schema()["required"])
    for field in _review_payload():
        if field in {"same_score_note"}:
            continue
        payload = _review_payload()
        payload.pop(field)
        try:
            MS._validate_review_payload(payload, 1)
        except ValueError:
            enforced = True
        else:
            enforced = False
        if enforced:
            assert field in published, f"{field} is enforced but not published"


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda d: d["scope"]["resources"].append(
            {"type": "record", "identifier": "has space", "access": "read",
             "constraints": []}
        ), "resource-identifier-whitespace"),
        (lambda d: d["scope"]["resources"].append(
            {"type": "path", "identifier": "x", "constraints": []}
        ), "resource-missing-access"),
        (lambda d: d["steps"][0].pop("acceptance_checks"), "step-missing-checks"),
        (lambda d: d["steps"][0].update({"depends_on": ["nope"]}), "unknown-dependency"),
    ],
)
def test_the_plan_validator_enforces_what_the_schema_describes(mutate, label, tmp_path):
    """The schema describes per-field rules, not only which keys are required.

    Removing whole keys leaves those rules untested: a validator could stop
    checking `access` or the identifier's whitespace and every key-level test
    would still pass.  Each case here corresponds to a sentence in the
    published `fields` or `rules`.
    """
    from plan_contract import PlanContractError, validate_plan_document

    document = _plan_document()
    mutate(document)

    with pytest.raises(PlanContractError):
        validate_plan_document(document, tmp_path)


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda p: p["findings"][0].pop("evidence"), "medium-without-evidence"),
        (lambda p: p["findings"][0].pop("axis"), "finding-without-axis"),
        (lambda p: p["findings"].append(dict(p["findings"][0])), "duplicate-finding-id"),
        (lambda p: p["findings"][0].update({"id": "B-1"}), "id-without-perspective-prefix"),
        (lambda p: p.update({"scores": {k: 0.9 for k in p["scores"]}}), "normalized-scale"),
    ],
)
def test_the_review_validator_enforces_what_the_schema_describes(mutate, label):
    """Same for the review contract."""
    payload = _review_payload()
    mutate(payload)

    with pytest.raises(ValueError):
        MS._validate_review_payload(payload, 1)


def test_the_published_enums_match_the_implementation():
    """Enum values are restated for readers; a drifted copy is worse than none.

    Every published enum is checked, not a chosen few: a set left out here is
    one that can drift without anything noticing.
    """
    from plan_contract import ACTION_TYPES, EFFECT_CLASSES, RESOURCE_TYPES, contract_schema

    enums = contract_schema()["enums"]
    expected = {
        "scope.resources[].type": set(RESOURCE_TYPES),
        "scope.actions[].type": set(ACTION_TYPES),
        "scope.actions[].effect_class": set(EFFECT_CLASSES),
        "steps[].action": set(ACTION_TYPES),
    }
    assert set(enums) == set(expected), "an enum is published but not checked"
    for key, values in expected.items():
        assert set(enums[key]) == values, key

    review_enums = MS.review_contract_schema()["enums"]
    review_expected = {
        "findings[].axis": set(MS.REVIEW_SCORE_KEYS),
        "findings[].severity": set(MS.REVIEW_SEVERITIES),
        "scores": set(MS.REVIEW_SCORE_KEYS),
    }
    assert set(review_enums) == set(review_expected)
    for key, values in review_expected.items():
        assert set(review_enums[key]) == values, key


def test_the_cli_prints_both_schemas(tmp_path, run_cli):
    """The point of the issue is discoverability without submitting a document."""
    for contract in ("planning-adopt-core", "review-import"):
        result = run_cli("schema", "--contract", contract, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["contract"] == contract
        assert payload["required"]


def test_every_halt_category_has_a_documented_recovery_step():
    """Recovering from a halt should not require reading the source.

    The table is bound to the enum so a new category cannot ship without its
    recovery step, and a removed one cannot linger as stale guidance.
    """
    from mission_common import HALT_CATEGORIES

    text = (ROOT / "refs" / "state-management.md").read_text(encoding="utf-8")
    section = text.split("## halt category ごとの復帰手順", 1)
    assert len(section) == 2, "recovery section is missing"
    body = section[1].split("\n## ", 1)[0]

    documented = set(re.findall(r"^\| `([a-z-]+)` \|", body, re.MULTILINE))
    assert documented == set(HALT_CATEGORIES)


def test_the_recovery_table_names_reactivate_for_every_category_that_accepts_it():
    """The table's content has to match behaviour, not just its category list.

    Matching the enum only proves each category has a row.  A row can still
    describe the wrong procedure, which is worse than no row: the reader
    follows it and it does not work.  `reactivate` refuses exactly one
    category, so every other row must name it.
    """
    from mission_common import HALT_CATEGORIES

    text = (ROOT / "refs" / "state-management.md").read_text(encoding="utf-8")
    body = text.split("## halt category ごとの復帰手順", 1)[1].split("\n## ", 1)[0]

    rows = dict(re.findall(r"^\| `([a-z-]+)` \|[^|]*\|([^|]*)\|", body, re.MULTILINE))
    assert set(rows) == set(HALT_CATEGORIES)

    for category, procedure in rows.items():
        if category == "stale":
            # The one category the command refuses.  The row has to send the
            # reader to `resume` and say so, not merely mention both.
            assert "resume" in procedure, category
            assert "使えない" in procedure, category
        else:
            assert "reactivate" in procedure, category
