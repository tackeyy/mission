"""Published input contracts for the commands that validate imperatively (#683).

``planning adopt-core`` and ``review-import`` both check their input field by
field and reject on the first problem, so the only way to learn the contract was
to submit a document and read the rejection.

Publishing the contract creates a second place the same rules live.  The tests
bind each description to its validator: a field listed as required that the
validator does not enforce, or an enum that has drifted from the implementation,
fails the suite.  The description is documentation, never the check itself.
"""
from __future__ import annotations

import copy
import json


def _review_contract_schema(score_keys, severities) -> dict:
    return {
        "schema": "mission-contract-schema/1",
        "contract": "review-import",
        "required": ["schema", "iteration", "perspective", "scores", "findings"],
        "fields": {
            "schema": 'must be exactly "mission-review/1"',
            "iteration": "int matching the --iteration argument",
            "perspective": "non-empty trimmed string; prefixes every finding id",
            "scores": (
                "object with exactly the four axes, or null for a findings-only "
                "reviewer"
            ),
            "findings[].id": 'string starting with "<perspective>-", unique',
            "findings[].severity": "one of the severity enum",
            "findings[].axis": "one of the four axes",
            "findings[].evidence": "required and non-empty for High and Medium",
            "scores[*]": (
                "finite number in 0..5.  A payload whose four axes all fall in "
                "0..1 is rejected as a normalized scale, because a normalized "
                "score silently reads as a near-zero raw one"
            ),
            "learning_schema": (
                "opt-in marker for the learning contract.  Without it, no "
                "finding may carry a learning field; any other key starting "
                "with learning_ is rejected outright"
            ),
            "findings[].cause / general_fix_rule / weak_phase": (
                "the learning fields.  Allowed only when learning_schema is "
                "present, and then validated by the learning contract"
            ),
            "same_score_note": (
                "required when all four scores are equal; states why the "
                "reviewer scored them the same"
            ),
        },
        "enums": {
            "findings[].axis": list(score_keys),
            "findings[].severity": sorted(severities),
            "scores": list(score_keys),
        },
        "rules": [
            "finding ids are unique within one review",
            "every finding carries an axis from the enum, not only a severity",
            "scores may be null only for a findings-only reviewer",
            "learning fields are validated by the review learning contract",
        ],
    }


def review_contract_schema() -> dict:
    """Return the published input contract for ``review-import``."""
    from scoring_provenance import REVIEW_SCORE_KEYS, REVIEW_SEVERITIES

    return copy.deepcopy(_review_contract_schema(REVIEW_SCORE_KEYS, REVIEW_SEVERITIES))


def contract_schema_for(contract: str) -> dict:
    """Return one published contract by name."""
    from plan_contract import contract_schema as plan_contract_schema

    if contract == "planning-adopt-core":
        return plan_contract_schema()
    if contract == "review-import":
        return review_contract_schema()
    raise ValueError("unknown contract: {}".format(contract))


def render_contract_schema(contract: str) -> str:
    """Render one published contract for printing."""
    return json.dumps(contract_schema_for(contract), ensure_ascii=False, indent=2)
