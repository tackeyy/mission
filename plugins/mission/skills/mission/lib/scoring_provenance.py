"""Portable validators for immutable scoring and forced-pass provenance."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERIFIER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ACTOR_RE = re.compile(r"^(?:role:[a-z][a-z0-9-]{0,63}|sha256:[0-9a-f]{64})$")
REASON_CODES = {"user-override", "safety-exception", "operational-recovery"}
MAX_APPROVAL_AGE = timedelta(days=1)
REQUEST_SCHEMA = "mission-force-approval-request/1"
RESPONSE_SCHEMA = "mission-force-approval-response/1"
TERMINAL_STATE_BINDING_SCHEMA = "mission-terminal-state-binding/1"
REVIEW_SCORE_KEYS = ("mission_achievement", "accuracy", "completeness", "usability")
REVIEW_SEVERITIES = {"High", "Medium", "Low"}


def _finite_score(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be a finite number")
    if not 0.0 <= float(value) <= 5.0:
        raise ValueError(f"{field} must be in the 0-5 range")
    return float(value)


def _finding_cap(findings: list[dict[str, object]]) -> float | None:
    counts = {severity: 0 for severity in REVIEW_SEVERITIES}
    for finding in findings:
        severity = finding["severity"]
        counts[severity] += 1
    if counts["High"]:
        return 3.0
    if counts["Medium"] >= 3:
        return 3.5
    if counts["Medium"]:
        return 4.0
    if counts["Low"] >= 4:
        return 4.3
    if counts["Low"] >= 2:
        return 4.5
    if counts["Low"]:
        return 4.7
    return None


def _consensus_score(max_delta: float) -> float:
    if max_delta <= 0.5:
        return 5.0
    if max_delta <= 1.0:
        return 4.0
    if max_delta <= 1.5:
        return 3.0
    if max_delta <= 2.0:
        return 2.0
    return 1.0


def reduce_review_aggregate(inputs: object, *, expected_iteration: int | None = None) -> dict[str, object]:
    """Re-derive every score gate field from archived review inputs.

    This deliberately accepts the already-normalized review archive shape, but
    still validates it independently.  Evidence is untrusted I/O at every
    later writer/audit boundary, not a cached assertion from aggregate time.
    """
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("review aggregate inputs must be a non-empty list")
    if expected_iteration is not None and (
        isinstance(expected_iteration, bool)
        or not isinstance(expected_iteration, int)
        or expected_iteration < 1
    ):
        raise ValueError("review aggregate expected iteration is invalid")
    perspectives: set[str] = set()
    adjusted: list[dict[str, object]] = []
    open_high = 0
    for index, review in enumerate(inputs):
        if not isinstance(review, dict):
            raise ValueError("review aggregate input must be an object")
        if expected_iteration is not None:
            if review.get("schema") != "mission-review/1":
                raise ValueError("review aggregate schema must be mission-review/1")
            iteration = review.get("iteration")
            if (isinstance(iteration, bool) or not isinstance(iteration, int)
                    or iteration < 1 or iteration != expected_iteration):
                raise ValueError("review aggregate iteration does not match expected iteration")
        perspective = review.get("perspective")
        if not isinstance(perspective, str) or not perspective.strip():
            raise ValueError("review aggregate perspective is invalid")
        if perspective in perspectives:
            raise ValueError("review aggregate has duplicate perspective")
        perspectives.add(perspective)
        findings = review.get("findings")
        if not isinstance(findings, list):
            raise ValueError("review aggregate findings must be a list")
        by_axis: dict[str, list[dict[str, object]]] = {key: [] for key in REVIEW_SCORE_KEYS}
        finding_ids: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("review aggregate finding must be an object")
            identifier, severity, axis = finding.get("id"), finding.get("severity"), finding.get("axis")
            if (not isinstance(identifier, str) or identifier in finding_ids or severity not in REVIEW_SEVERITIES
                    or axis not in REVIEW_SCORE_KEYS):
                raise ValueError("review aggregate finding is invalid")
            finding_ids.add(identifier)
            if severity == "High":
                open_high += 1
            by_axis[axis].append({"severity": severity})
        scores = review.get("scores")
        if scores is None:
            continue
        if not isinstance(scores, dict) or set(scores) != set(REVIEW_SCORE_KEYS):
            raise ValueError("review aggregate scores must contain canonical axes")
        normalized = {axis: _finite_score(scores[axis], field=f"review score {axis}") for axis in REVIEW_SCORE_KEYS}
        values = list(normalized.values())
        note = review.get("same_score_note")
        if len(set(values)) == 1 and (not isinstance(note, str) or not note.strip()):
            raise ValueError("review aggregate same score note is required")
        if len(set(values)) == 1 and isinstance(note, str) and (
            "全体印象" in note or "overall impression" in note.lower()
        ):
            continue
        for axis in REVIEW_SCORE_KEYS:
            cap = _finding_cap(by_axis[axis])
            if cap is not None and normalized[axis] > cap:
                normalized[axis] = cap
        adjusted.append({"perspective": perspective, "scores": normalized})
    if not adjusted:
        raise ValueError("review aggregate has no scoring reviewers")
    values_by_axis = {axis: [entry["scores"][axis] for entry in adjusted] for axis in REVIEW_SCORE_KEYS}
    items = {axis: round(sum(values) / len(values), 2) for axis, values in values_by_axis.items()}
    detail = {
        axis: {"min": round(min(values), 2), "max": round(max(values), 2), "delta": round(max(values) - min(values), 2)}
        for axis, values in values_by_axis.items()
    }
    agreement = None
    if len(adjusted) >= 2:
        agreement = _consensus_score(max(item["delta"] for item in detail.values()))
    numeric_items = list(items.values())
    # Agreement is a gate observation, not a fifth quality axis.  Keeping the
    # score vector fixed makes composite/min semantics invariant across review
    # tiers and prevents an old consensus value from changing a new score.
    return {
        "items": items, "composite": round(sum(numeric_items) / len(numeric_items), 2),
        "min_item": round(min(numeric_items), 2), "open_high": open_high,
        "review_agreement": agreement, "agreement_detail": detail,
    }


def terminal_state_projection(state: object) -> dict[str, object]:
    """Return the versioned, stable subset a force approval is allowed to bind."""
    if not isinstance(state, dict):
        raise ValueError("terminal state is invalid")
    history = state.get("score_history", [])
    if not isinstance(history, list):
        raise ValueError("terminal state score_history is invalid")
    score_fields = (
        "iteration", "items", "composite", "min_item", "open_high", "review_agreement",
        "agreement_detail", "score_provenance", "revision_scope", "score_source",
    )
    return {
        "schema": TERMINAL_STATE_BINDING_SCHEMA,
        "session_id": state.get("session_id"), "mission_id": state.get("mission_id"),
        "iteration": state.get("iteration"), "threshold": state.get("threshold"),
        "revision_scope": state.get("revision_scope"),
        "score_history": [{key: entry.get(key) for key in score_fields} if isinstance(entry, dict) else entry for entry in history],
        "passes": state.get("passes"), "loop_active": state.get("loop_active"),
        "passes_forced": state.get("passes_forced"), "terminal_outcome": state.get("terminal_outcome"),
        "halt_reason": state.get("halt_reason"), "halt_category": state.get("halt_category"),
    }


def terminal_state_digest(state: object) -> str:
    return digest(terminal_state_projection(state))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _timestamp(value: object, *, now: datetime | None = None, require_fresh: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError("approval timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("approval timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("approval timestamp is invalid")
    current = now or datetime.now(timezone.utc)
    if parsed > current or (require_fresh and current - parsed > MAX_APPROVAL_AGE):
        raise ValueError("approval timestamp is expired or in the future")
    return value


def _receipt_ref(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("kind") != "approval-receipt":
        raise ValueError("approval receipt reference is invalid")
    path, value_digest = value.get("path"), value.get("digest")
    if (not isinstance(path, str) or not path or path.startswith("/") or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not SHA256_REF_RE.fullmatch(str(value_digest or ""))):
        raise ValueError("approval receipt reference is invalid")
    return {"kind": "approval-receipt", "path": path, "digest": value_digest}


def build_request(*, session_id: object, mission_id: object, revision_scope: object,
                  terminal_object_digest: object, approval_evidence_ref: object,
                  approved_actor: object, approved_at: object, reason_code: object,
                  event_nonce: object, now: datetime | None = None,
                  require_fresh: bool = True) -> dict[str, str | dict]:
    if not (isinstance(session_id, str) and session_id and isinstance(mission_id, str) and mission_id
            and isinstance(revision_scope, dict) and SHA256_REF_RE.fullmatch(str(terminal_object_digest or ""))
            and SHA256_REF_RE.fullmatch(str(approval_evidence_ref or ""))
            and isinstance(approved_actor, str) and ACTOR_RE.fullmatch(approved_actor)
            and reason_code in REASON_CODES and isinstance(event_nonce, str)
            and re.fullmatch(r"[0-9a-f]{32,128}", event_nonce)):
        raise ValueError("--approval-evidence-ref, opaque --approved-actor role, --approved-at, and --reason-code are required")
    request: dict[str, str | dict] = {
        "schema": REQUEST_SCHEMA, "session_id": session_id, "mission_id": mission_id,
        "revision_scope": revision_scope, "terminal_object_digest": terminal_object_digest,
        "approval_evidence_ref": approval_evidence_ref, "approved_actor": approved_actor,
        "approved_at": _timestamp(approved_at, now=now, require_fresh=require_fresh), "reason_code": reason_code,
        "event_nonce": event_nonce,
    }
    request["request_digest"] = digest(request)
    return request


def validate_request(value: object, *, now: datetime | None = None,
                     require_fresh: bool = True) -> dict[str, str | dict]:
    if not isinstance(value, dict):
        raise ValueError("approval request is invalid")
    expected = build_request(**{key: value.get(key) for key in (
        "session_id", "mission_id", "revision_scope", "terminal_object_digest", "approval_evidence_ref",
        "approved_actor", "approved_at", "reason_code", "event_nonce",
    )}, now=now, require_fresh=require_fresh)
    if value != expected:
        raise ValueError("approval request is not canonical")
    return expected


def validate_recorded_envelope(value: object, *, now: datetime | None = None,
                              require_fresh: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"request", "response", "receipt_ref", "consumed"}:
        raise ValueError("approval envelope is invalid")
    request = validate_request(value["request"], now=now, require_fresh=require_fresh)
    response = value["response"]
    if not isinstance(response, dict) or set(response) != {"schema", "decision", "verifier_id", "request_digest", "receipt_ref", "verified_at"}:
        raise ValueError("approval response is invalid")
    receipt = _receipt_ref(value["receipt_ref"])
    if (response.get("schema") != RESPONSE_SCHEMA or response.get("decision") != "approved"
            or not isinstance(response.get("verifier_id"), str) or not VERIFIER_ID_RE.fullmatch(response["verifier_id"])
            or response.get("request_digest") != request["request_digest"]
            or response.get("receipt_ref") != receipt or not isinstance(value["consumed"], bool)
            or not value["consumed"]):
        raise ValueError("approval response is invalid")
    _timestamp(response.get("verified_at"), now=now, require_fresh=require_fresh)
    return {"request": request, "response": response, "receipt_ref": receipt, "consumed": True}


def read_state_local_bytes(root: object, path_text: object, *, limit: int = 4 * 1024 * 1024) -> bytes:
    """Read one state-local regular file through no-follow descriptors."""
    if not isinstance(root, (str, os.PathLike)) or not isinstance(path_text, str) or "\x00" in path_text:
        raise ValueError("approval receipt path is invalid")
    raw = Path(path_text)
    if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts) or not raw.parts or raw.parts[0] != ".mission-state":
        raise ValueError("approval receipt path is invalid")
    fd = os.open(os.fspath(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for index, part in enumerate(raw.parts):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if index + 1 < len(raw.parts):
                flags |= getattr(os, "O_DIRECTORY", 0)
            else:
                flags |= os.O_NONBLOCK
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("approval receipt must be a bounded regular file")
        content = os.read(fd, info.st_size + 1)
        if len(content) != info.st_size or os.fstat(fd).st_size != info.st_size:
            raise ValueError("approval receipt changed while being read")
        content.decode("utf-8")
        return content
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("approval receipt path is invalid") from exc
    finally:
        os.close(fd)


def validate_receipt_binding(root: object, envelope: object) -> None:
    """Validate the immutable receipt payload against its canonical request."""
    validated = validate_recorded_envelope(envelope, require_fresh=False)
    receipt = validated["receipt_ref"]
    content = read_state_local_bytes(root, receipt["path"])
    if "sha256:" + hashlib.sha256(content).hexdigest() != receipt["digest"]:
        raise ValueError("approval receipt digest mismatch")
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("approval receipt is invalid") from exc
    request = validated["request"]
    expected = {
        "schema": "mission-force-approval-receipt/1",
        "session_id": request["session_id"], "mission_id": request["mission_id"],
        "revision_scope": request["revision_scope"],
        "terminal_object_digest": request["terminal_object_digest"],
        "event_nonce": request["event_nonce"], "request_digest": request["request_digest"],
    }
    if document != expected:
        raise ValueError("approval receipt binding is invalid")
