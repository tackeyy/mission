"""Published input contracts for the commands that validate imperatively (#683).

``planning adopt-core`` and ``review-import`` both check their input field by
field and reject on the first problem, so the only way to learn the contract was
to submit a document and read the rejection.

Publishing the contract creates a second place the same rules live.  The
`test_bound` list names the propositions a test actually holds -- an id with no
test, or an enum whose values the validator does not enforce, fails the suite.

`fields` and `rules` describe the validator without that guarantee.  Claiming
otherwise would be the same mistake this exists to prevent: a description
asserting more than the implementation supports.
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
        # See the note on the plan contract: `fields` and `rules` describe the
        # validator, but only these ids have a test that fails when the check
        # is removed.
        "binding_note": (
            "fields and rules describe the validator.  Only the ids in test_bound "
            "have a test that fails when the corresponding check is removed."
        ),
        "test_bound": [
            "required-fields",
            "enums",
            "finding-id-perspective-prefix",
            "finding-id-uniqueness",
            "evidence-required-for-high-and-medium",
            "finding-axis-required",
            "score-range",
            "normalized-scale-rejected",
            "learning-schema-marker",
            "unknown-learning-key",
        ],
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


def acceptance_contract_schema() -> dict:
    """Return the public schema for immutable acceptance contract import."""
    from acceptance_contract import REVIEW_POLICY, SCHEMA

    return {
        "schema": "mission-contract-schema/1",
        "contract": "acceptance-contract-import",
        "required": ["schema", "mission_id", "requirement_text", "requirement_digest", "revision", "review_policy", "requirements", "criteria", "coverage"],
        "enums": {
            "schema": [SCHEMA],
            "review_policy": [REVIEW_POLICY],
            "coverage.status": ["pending"],
            "requirements[].classification": ["obligation", "context"],
            "criteria[].verification_kind": ["command"],
        },
        "fields": {
            "mission_id": "non-empty mission or session identifier; must equal the state mission_id or session_id",
            "requirement_text": "non-empty UTF-8 text without NUL or surrogate code points",
            "requirement_digest": "sha256:<64 lowercase hexadecimal>, computed from requirement_text UTF-8 bytes",
            "revision": "integer at least 1",
            "requirements[]": "contiguous ledger entries covering requirement_text exactly",
            "requirements[].id": "unique non-empty identifier",
            "requirements[].start / end": "integer Unicode codepoint indexes; start begins at the previous end and end is greater than start",
            "requirements[].text": "exact substring of requirement_text from start through end",
            "requirements[].classification": "obligation or context",
            "criteria[]": "non-empty list of unique acceptance criteria; unmapped obligations remain retained with coverage pending",
            "criteria[].id": "unique non-empty identifier",
            "criteria[].requirement_ids": "non-empty list of identifiers from requirements[]",
            "criteria[].expected": "non-empty expected result",
            "criteria[].required": "boolean",
            "criteria[].prohibited_side_effects": "list of non-empty strings",
            "criteria[].verification_kind": "command",
            "criteria[].target_path": "normalized non-absolute project-relative path",
            "criteria[].command_id": "non-empty frozen verifier-policy identifier; its registered policy is resolved when a verification receipt is recorded",
            "coverage": 'exactly {"status":"pending"} at import',
        },
    }


def contract_schema_for(contract: str) -> dict:
    """Return one published contract by name."""
    from plan_contract import contract_schema as plan_contract_schema

    if contract == "planning-adopt-core":
        return plan_contract_schema()
    if contract == "review-import":
        return review_contract_schema()
    if contract == "fresh-review-prepare":
        from mission_kernel.fresh_review import BUDGET_LIMITS, FreshReviewRequest, REQUEST_SCHEMA
        return {"schema": "mission-contract-schema/1", "contract": contract,
                "request_schema": REQUEST_SCHEMA, "required": list(FreshReviewRequest.__dataclass_fields__),
                "closed": True, "budget_limits": dict(BUDGET_LIMITS),
                "rules": ["request_id and nonce are generated by the application",
                          "MISSION_OPERATION_ID replays only the stored identical intent and payload",
                          "candidate_digest binds the command-ID-ordered snapshot map",
                          "prepare does not launch a runtime or satisfy the completion gate"]}
    if contract == "fresh-review-withdraw":
        from mission_kernel.fresh_review import WithdrawnFreshReviewRecord
        return {"schema": "mission-contract-schema/1", "contract": contract, "closed": True,
                "required": ["request_id"], "record_fields": list(WithdrawnFreshReviewRecord.__dataclass_fields__),
                "rules": ["pending requests only; over capacity and strictly shrinking",
                          "same-operation replay returns the immutable withdrawn tombstone"]}
    if contract in ("fresh-review-run", "fresh-review-reconcile"):
        from mission_kernel.fresh_review_receipts import LAUNCH_SCHEMA, TERMINAL_SCHEMA
        return {"schema": "mission-contract-schema/1", "contract": contract, "closed": True,
                "required": ["request", "adapter"], "launch_schema": LAUNCH_SCHEMA,
                "terminal_schema": TERMINAL_SCHEMA, "terminal_outcomes": ["blocked", "abandoned-unknown"],
                "rules": ["durable dispatch intent precedes launch",
                          "reconcile never redispatches and requires exact host-observed child and output",
                          "dispatch identity and current commit fence are separate",
                          "output import, replay and completed publication are not available"]}
    if contract == "acceptance-contract-import":
        return acceptance_contract_schema()
    raise ValueError("unknown contract: {}".format(contract))


def render_contract_schema(contract: str) -> str:
    """Render one published contract for printing."""
    return json.dumps(contract_schema_for(contract), ensure_ascii=False, indent=2)
