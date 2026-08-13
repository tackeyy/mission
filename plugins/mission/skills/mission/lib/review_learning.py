"""Strict review-learning metadata and deterministic failure-ledger reduction."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from mission_common import classify_state, session_role


LEARNING_SCHEMA = "mission-review-learning/1"
LEARNING_BRIEF_SCHEMA = "mission-learning-brief/1"
LEDGER_SCHEMA = "mission-failure-ledger/1"
WEAK_PHASES = ("understanding", "planning", "execution", "formatting")
_LEARNING_FIELDS = {"cause", "general_fix_rule", "weak_phase"}
_SHA256_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_TEXT = 4096


class LearningContractError(ValueError):
    """Untrusted review-learning or ledger data violates the contract."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise LearningContractError(f"{field} must be a non-empty bounded string")
    if any(ord(char) < 32 for char in value):
        raise LearningContractError(f"{field} contains a control character")
    return value.strip()


def normalize_general_fix_rule(value: object) -> str:
    return " ".join(_text(value, "general_fix_rule").split()).casefold()


def learning_identity(weak_phase: object, general_fix_rule: object) -> str:
    if weak_phase not in WEAK_PHASES:
        raise LearningContractError("weak_phase is invalid")
    normalized = normalize_general_fix_rule(general_fix_rule)
    return "sha256:" + hashlib.sha256(
        json.dumps([weak_phase, normalized], separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def validate_review_learning(review: object) -> None:
    if not isinstance(review, Mapping):
        raise LearningContractError("review must be an object")
    marker = review.get("learning_schema")
    if any(isinstance(key, str) and key.startswith("learning_") and key != "learning_schema" for key in review):
        raise LearningContractError("unknown learning field")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise LearningContractError("findings must be a list")
    if marker is None:
        if any(isinstance(finding, Mapping) and _LEARNING_FIELDS.intersection(finding) for finding in findings):
            raise LearningContractError("learning fields require learning_schema")
        return
    if marker != LEARNING_SCHEMA:
        raise LearningContractError("learning_schema is invalid")
    for index, finding in enumerate(findings, 1):
        if not isinstance(finding, Mapping):
            raise LearningContractError(f"finding {index} must be an object")
        missing = _LEARNING_FIELDS - set(finding)
        if missing:
            raise LearningContractError(f"finding {index} missing {sorted(missing)[0]}")
        _text(finding.get("cause"), "cause")
        normalize_general_fix_rule(finding.get("general_fix_rule"))
        if finding.get("weak_phase") not in WEAK_PHASES:
            raise LearningContractError("weak_phase is invalid")
        if any(isinstance(key, str) and key.startswith("learning_") for key in finding):
            raise LearningContractError("unknown finding learning field")


def _aggregate_ref(value: object) -> tuple[str, str]:
    if not isinstance(value, Mapping) or value.get("kind") != "review-aggregate":
        raise LearningContractError("review aggregate reference is invalid")
    digest = value.get("digest")
    if not isinstance(digest, str) or _SHA256_REF.fullmatch(digest) is None:
        raise LearningContractError("review aggregate digest is invalid")
    return "review-aggregate", digest


def reduce_failure_ledger(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    patterns: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise LearningContractError("learning observation is invalid")
        iteration = observation.get("iteration")
        if type(iteration) is not int or iteration < 1:
            raise LearningContractError("learning iteration is invalid")
        review = observation.get("review")
        validate_review_learning(review)
        if not isinstance(review, Mapping) or review.get("learning_schema") != LEARNING_SCHEMA:
            continue
        _kind, digest = _aggregate_ref(observation.get("review_aggregate_ref"))
        for finding in review["findings"]:
            phase = finding["weak_phase"]
            rule = normalize_general_fix_rule(finding["general_fix_rule"])
            identity = learning_identity(phase, rule)
            pattern = patterns.setdefault(identity, {
                "pattern_id": identity, "weak_phase": phase, "general_fix_rule": rule,
                "iterations": [], "recurrence_count": 0, "examples": [],
            })
            if iteration not in pattern["iterations"]:
                pattern["iterations"].append(iteration)
                pattern["examples"].append({"iteration": iteration, "review_aggregate_digest": digest})
    for pattern in patterns.values():
        pattern["iterations"].sort()
        pattern["examples"].sort(key=lambda item: (item["iteration"], item["review_aggregate_digest"]))
        pattern["recurrence_count"] = max(0, len(pattern["iterations"]) - 1)
    return {"schema": LEDGER_SCHEMA, "patterns": [patterns[key] for key in sorted(patterns)]}


def validate_failure_ledger(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "patterns"} or value.get("schema") != LEDGER_SCHEMA:
        raise LearningContractError("failure ledger is invalid")
    patterns = value.get("patterns")
    if not isinstance(patterns, list):
        raise LearningContractError("failure ledger patterns are invalid")
    seen: set[str] = set()
    for pattern in patterns:
        if (not isinstance(pattern, Mapping)
                or set(pattern) != {"pattern_id", "weak_phase", "general_fix_rule", "iterations", "recurrence_count", "examples"}
                or pattern.get("pattern_id") in seen):
            raise LearningContractError("failure ledger pattern is invalid")
        identity = learning_identity(pattern.get("weak_phase"), pattern.get("general_fix_rule"))
        if pattern.get("pattern_id") != identity:
            raise LearningContractError("failure ledger pattern identity is invalid")
        seen.add(identity)
        iterations = pattern.get("iterations")
        examples = pattern.get("examples")
        if (not isinstance(iterations, list) or not iterations or iterations != sorted(set(iterations))
                or any(type(item) is not int or item < 1 for item in iterations)
                or type(pattern.get("recurrence_count")) is not int
                or pattern["recurrence_count"] != len(iterations) - 1
                or not isinstance(examples, list) or len(examples) != len(iterations)):
            raise LearningContractError("failure ledger occurrence is invalid")
        example_iterations: set[int] = set()
        for example in examples:
            if (not isinstance(example, Mapping) or set(example) != {"iteration", "review_aggregate_digest"}
                    or example.get("iteration") not in iterations or not isinstance(example.get("review_aggregate_digest"), str)
                    or _SHA256_REF.fullmatch(example["review_aggregate_digest"]) is None):
                raise LearningContractError("failure ledger example is invalid")
            if example["iteration"] in example_iterations:
                raise LearningContractError("failure ledger example is duplicated")
            example_iterations.add(example["iteration"])
    return dict(value)


def failure_ledger_counts(states: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    phase_counts: Counter[str] = Counter()
    pattern_count = recurring = invalid = 0
    for state in states:
        ledger = state.get("failure_ledger") if isinstance(state, Mapping) else None
        if ledger is None:
            continue
        try:
            validated = validate_failure_ledger(ledger)
        except LearningContractError:
            invalid += 1
            continue
        for pattern in validated["patterns"]:
            pattern_count += 1
            phase_counts[pattern["weak_phase"]] += len(pattern["iterations"])
            if pattern["recurrence_count"] > 0:
                recurring += 1
    return {
        "pattern_count": pattern_count, "recurring_pattern_count": recurring,
        "weak_phase_counts": dict(sorted(phase_counts.items())), "invalid_ledger_count": invalid,
    }


def _valid_score_entries(score_history: object) -> list[Mapping[str, Any]]:
    if not isinstance(score_history, list):
        return []
    entries: list[Mapping[str, Any]] = []
    for entry in score_history:
        if not isinstance(entry, Mapping):
            continue
        composite = entry.get("composite")
        if (
            isinstance(composite, (int, float))
            and not isinstance(composite, bool)
            and math.isfinite(float(composite))
        ):
            entries.append(entry)
    return entries


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def reduce_iteration_recovery(states: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sessions_with_reject = 0
    deltas: list[float] = []
    iterations: list[float] = []
    resolved_findings = 0.0
    first_findings = 0.0
    for state in states:
        if not isinstance(state, Mapping):
            continue
        if session_role(state) != "implementer" or classify_state(state) != "pass":
            continue
        score_history = _valid_score_entries(state.get("score_history"))
        if len(score_history) < 2:
            continue
        sessions_with_reject += 1
        first_entry = score_history[0]
        final_entry = score_history[-1]
        first_composite = float(first_entry["composite"])
        final_composite = float(final_entry["composite"])
        deltas.append(final_composite - first_composite)
        iterations.append(float(len(score_history)))
        first_open_high = first_entry.get("open_high")
        final_open_high = final_entry.get("open_high")
        if (
            isinstance(first_open_high, int)
            and not isinstance(first_open_high, bool)
            and first_open_high > 0
            and isinstance(final_open_high, int)
            and not isinstance(final_open_high, bool)
            and final_open_high >= 0
        ):
            resolved_findings += float(max(0, first_open_high - final_open_high))
            first_findings += float(first_open_high)
    return {
        "sessions_with_reject": sessions_with_reject,
        "first_to_final_composite_delta": {
            "mean": _mean(deltas),
            "median": _median(deltas),
        },
        "avg_iterations": _mean(iterations),
        "resolved_findings_ratio": (resolved_findings / first_findings) if first_findings else None,
    }


def summarize_learning_brief(
    states: Sequence[Mapping[str, Any]], *, weak_phase: str | None = None, limit: int = 10,
) -> dict[str, Any]:
    if weak_phase is not None and weak_phase not in WEAK_PHASES:
        raise LearningContractError("weak_phase is invalid")
    if type(limit) is not int or limit < 0:
        raise LearningContractError("limit is invalid")
    patterns: dict[tuple[str, str], dict[str, Any]] = {}
    for state in states:
        if not isinstance(state, Mapping):
            raise LearningContractError("learning state is invalid")
        ledger = state.get("failure_ledger")
        if ledger is None:
            continue
        try:
            validated = validate_failure_ledger(ledger)
        except LearningContractError:
            continue
        for pattern in validated["patterns"]:
            phase = pattern["weak_phase"]
            if weak_phase is not None and phase != weak_phase:
                continue
            rule = normalize_general_fix_rule(pattern["general_fix_rule"])
            key = (phase, rule)
            bucket = patterns.setdefault(
                key,
                {
                    "general_fix_rule": rule,
                    "weak_phase": phase,
                    "recurrence": 0,
                    "sessions": 0,
                },
            )
            bucket["recurrence"] += int(pattern["recurrence_count"])
            bucket["sessions"] += 1
    rules = sorted(
        patterns.values(),
        key=lambda item: (-item["recurrence"], -item["sessions"], item["weak_phase"], item["general_fix_rule"]),
    )
    return {"schema": LEARNING_BRIEF_SCHEMA, "rules": rules[:limit]}
