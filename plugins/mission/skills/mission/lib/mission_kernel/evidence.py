"""Pure projections for progress, context, and verification evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from .commands import (
    ClearProgress,
    GenerateContextManifest,
    GenerateClaimsLedger,
    RecordVerification,
    UpdateProgress,
    VerificationCheck,
)


class EvidenceRuleError(ValueError):
    """Stable rejection raised by evidence projection rules."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: object, code: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or (not allow_empty and not value):
        raise EvidenceRuleError(code)
    return value


def _relative_target(value: object, code: str) -> str:
    text = _text(value, code)
    if text.startswith("/") or "\\" in text:
        raise EvidenceRuleError(code)
    if any(part in {"", ".", ".."} for part in text.split("/")):
        raise EvidenceRuleError(code)
    return text


def _progress_bytes(state: Mapping[str, object], progress: dict, iteration: int) -> bytes:
    lines = [
        f"<!-- mission-progress-meta: session_id={state.get('session_id')} mission_id={state.get('mission_id')} iteration={iteration} updated_at={progress.get('updated_at')} -->",
        "",
        "# Mission Progress Checkpoint",
        "",
        f"- kind: {progress.get('kind')}",
        f"- total: {progress.get('total')}",
        f"- completed: {progress.get('completed')}",
        f"- remaining: {progress.get('remaining')}",
        f"- batch_size: {progress.get('batch_size')}",
        f"- last_unit: {progress.get('last_unit') or ''}",
        f"- artifact_path: {progress.get('artifact_path') or ''}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def project_progress_update(
    state: Mapping[str, object], command: UpdateProgress
) -> tuple[dict, bytes]:
    at = _text(command.at, "timestamp-invalid")
    if (
        type(command.total) is not int
        or type(command.completed) is not int
        or command.total < 0
        or not 0 <= command.completed <= command.total
    ):
        raise EvidenceRuleError("progress-bounds-invalid")
    if type(command.iteration) is not int or command.iteration < 0:
        raise EvidenceRuleError("progress-iteration-invalid")
    if command.batch_size is not None and (
        type(command.batch_size) is not int or command.batch_size < 0
    ):
        raise EvidenceRuleError("progress-batch-size-invalid")
    for value, code in (
        (command.last_unit, "progress-last-unit-invalid"),
        (command.artifact_path, "progress-artifact-path-invalid"),
    ):
        if value is not None:
            _text(value, code)
    target = _relative_target(
        getattr(command.effect, "target", None), "progress-evidence-path-invalid"
    )
    progress = {
        "kind": "batch",
        "total": command.total,
        "completed": command.completed,
        "remaining": command.total - command.completed,
        "batch_size": command.batch_size,
        "last_unit": command.last_unit,
        "artifact_path": command.artifact_path,
        "updated_at": at,
        "evidence_path": target,
    }
    return progress, _progress_bytes(state, progress, command.iteration)


def apply_progress_update(
    state: Mapping[str, object], command: UpdateProgress
) -> tuple[dict, bytes]:
    document = copy.deepcopy(dict(state))
    progress, content = project_progress_update(document, command)
    document["progress"] = progress
    document["updated_at"] = command.at
    return document, content


def apply_progress_clear(
    state: Mapping[str, object], command: ClearProgress
) -> dict:
    at = _text(command.at, "timestamp-invalid")
    document = copy.deepcopy(dict(state))
    document.pop("progress", None)
    document["updated_at"] = at
    return document


# #690: the only value push-score writes.  Comparing against it -- rather
# than testing the marker for truthiness -- is what keeps a forged or legacy
# marker from claiming that the projection behind it is trustworthy.
FINDINGS_SUMMARY_SOURCE = "review-aggregate"


# The severities the projection can emit.  Restated here rather than imported
# so the kernel keeps deciding on its own inputs, and so a value the projection
# never writes cannot claim that the entry behind it is complete.
PRIOR_FINDING_SEVERITIES = frozenset({"High", "Medium", "Low"})

# The projection always emits an axis, because the archive validator requires
# one.  A finding without a usable axis therefore did not come from the
# producer, whatever the source marker says.
PRIOR_FINDING_AXES = frozenset(
    {"mission_achievement", "accuracy", "completeness", "usability"}
)


def _is_usable_prior_finding(item: object) -> bool:
    """Whether one projected finding is one the producer could have written.

    The conditions mirror what the projection emits, which in turn mirrors what
    the archive validator accepts.  Requiring more here would report ``partial``
    for entries the producer legitimately wrote (#690).
    """
    if not isinstance(item, Mapping):
        return False
    return (
        isinstance(item.get("id"), str)
        and item.get("severity") in PRIOR_FINDING_SEVERITIES
        and item.get("axis") in PRIOR_FINDING_AXES
    )


def context_output_path_text(publication_path: object) -> str:
    """Return the manifest output path once it is known to be usable text.

    This runs before the path is made relative to the project: a value that
    is not a path at all is refused by name here, and only a path that would
    land in the wrong place is refused later by the publication rule.
    """
    path_text = _text(publication_path, "context-output-path-invalid")
    if not Path(path_text).name or Path(path_text).name in {".", ".."}:
        raise EvidenceRuleError("context-output-path-invalid")
    return path_text


def project_context_manifest(
    state: Mapping[str, object],
    *,
    iteration: object,
    publication_path: object,
    at: object,
) -> tuple[dict, bytes, int]:
    timestamp = _text(at, "timestamp-invalid")
    if type(iteration) is not int or iteration < 1:
        raise EvidenceRuleError("context-iteration-invalid")
    path_text = context_output_path_text(publication_path)
    prior_findings: list[dict] = []
    history = state.get("score_history")
    if history is not None and not isinstance(history, list):
        raise EvidenceRuleError("context-score-history-invalid")
    entries = [entry for entry in history or [] if isinstance(entry, Mapping)]
    supplied = 0
    for entry in entries:
        raw = entry.get("findings_summary")
        if raw is not None and not isinstance(raw, list):
            raise EvidenceRuleError("context-findings-invalid")
        findings = raw if isinstance(raw, list) else []
        # #690: "supplied" has to mean the projection is usable, not merely
        # that something is present.  A marker alone would let any truthy
        # value, a missing list, or an unusable element promote the manifest to
        # "complete" -- and "complete" is what tells a reviewer it may narrow
        # its search.  Anything short of the shape this projection writes falls
        # back to "partial", which claims nothing.
        supplied += (
            entry.get("findings_summary_source") == FINDINGS_SUMMARY_SOURCE
            and isinstance(raw, list)
            and all(_is_usable_prior_finding(item) for item in raw)
        )
        prior_findings.extend(
            copy.deepcopy(dict(item))
            for item in findings
            if isinstance(item, Mapping)
        )
    if not entries:
        status = "no-history"
    elif supplied == len(entries):
        status = "complete"
    else:
        status = "partial"
    manifest = {
        "schema": "mission-context-manifest/1",
        "iteration": iteration,
        "mission_goal": state.get("mission", ""),
        "mission_id": state.get("mission_id", ""),
        "assumptions_path": state.get("assumptions_path", ""),
        "prior_findings": prior_findings,
        # "no-history" means nothing has been scored yet; "partial" means at
        # least one entry carries no producer marker, so an empty list here is
        # not evidence that the reviewers found nothing.
        "prior_findings_status": status,
    }
    content = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    record = {
        "path": path_text,
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        "generated_at": timestamp,
    }
    return record, content, len(prior_findings)


def apply_context_manifest(
    state: Mapping[str, object], command: GenerateContextManifest
) -> tuple[dict, bytes, int]:
    document = copy.deepcopy(dict(state))
    record, content, findings_count = project_context_manifest(
        document,
        iteration=command.iteration,
        publication_path=getattr(command.effect, "publication_path", None),
        at=command.at,
    )
    records = document.get("context_manifests")
    records = copy.deepcopy(records) if isinstance(records, Mapping) else {}
    records[str(command.iteration)] = record
    document["context_manifests"] = records
    return document, content, findings_count


def apply_claims_ledger(state: Mapping[str, object], command: GenerateClaimsLedger) -> dict:
    timestamp = _text(command.at, "timestamp-invalid")
    if type(command.iteration) is not int or command.iteration < 1:
        raise EvidenceRuleError("claims-ledger-iteration-invalid")
    if not isinstance(command.doc_digest, str) or not __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", command.doc_digest):
        raise EvidenceRuleError("claims-ledger-doc-digest-invalid")
    claim = command.effect
    if not isinstance(claim.publication_path, str) or not claim.publication_path or Path(claim.publication_path).name != claim.target:
        raise EvidenceRuleError("claims-ledger-effect-claim-invalid")
    document = copy.deepcopy(dict(state))
    records = document.get("claims_ledgers")
    records = copy.deepcopy(records) if isinstance(records, Mapping) else {}
    records[str(command.iteration)] = {"path": claim.publication_path, "digest": claim.digest,
                                        "doc_digest": command.doc_digest, "generated_at": timestamp}
    document["claims_ledgers"] = records
    return document


def project_verification_entry(command: RecordVerification) -> dict:
    at = _text(command.at, "verification-timestamp-invalid")
    if type(command.iteration) is not int:
        raise EvidenceRuleError("verification-iteration-invalid")
    if type(command.checks) is not tuple:
        raise EvidenceRuleError("verification-checks-invalid")
    if command.kind not in ("execution", "implementation-read"):
        raise EvidenceRuleError("verification-kind-invalid")
    checks = []
    for check in command.checks:
        if type(check) is not VerificationCheck:
            raise EvidenceRuleError("verification-check-invalid")
        if not isinstance(check.name, str) or not check.name or "\x00" in check.name:
            raise EvidenceRuleError("verification-check-name-invalid")
        if type(check.ok) is not bool:
            raise EvidenceRuleError("verification-check-ok-invalid")
        if check.detail is not None and not isinstance(check.detail, str):
            raise EvidenceRuleError("verification-check-detail-invalid")
        checks.append({"name": check.name, "ok": check.ok, "detail": check.detail})
    implementation_checks = [
        check["name"].startswith("implementation-verified:") for check in checks
    ]
    if command.kind == "implementation-read" and not all(implementation_checks):
        raise EvidenceRuleError("verification-kind-check-mismatch")
    if command.kind == "execution" and any(implementation_checks):
        raise EvidenceRuleError("verification-kind-check-mismatch")
    failed_count = sum(1 for check in checks if not check["ok"])
    status = "not-run" if not checks else "failed" if failed_count else "passed"
    return {
        "iteration": command.iteration,
        "kind": command.kind,
        "status": status,
        "checks": checks,
        "failed_count": failed_count,
        "recorded_at": at,
    }


def apply_verification_record(
    state: Mapping[str, object], command: RecordVerification
) -> tuple[dict, dict]:
    document = copy.deepcopy(dict(state))
    entry = project_verification_entry(command)
    history = document.get("verification_history")
    history = copy.deepcopy(history) if isinstance(history, list) else []
    history.append(entry)
    document["verification_history"] = history
    document["updated_at"] = entry["recorded_at"]
    return document, entry
