"""Pure claim-ledger projection and fail-closed Git observation port."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


CLAIM_PREFIX = "implementation-verified:"
LEDGER_SCHEMA = "mission-claims-ledger/1"


def parse_claim_detail(value: object) -> dict:
    """Decode the closed implementation-claim grammar or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("implementation-claim-detail-invalid")
    try:
        detail = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("implementation-claim-detail-invalid") from exc
    required = {"repo", "path", "start", "end", "commit", "blob", "doc_digest", "claim"}
    if not isinstance(detail, dict) or set(detail) != required:
        raise ValueError("implementation-claim-detail-invalid")
    path = detail.get("path")
    if (
        detail.get("repo") != "self"
        or not isinstance(path, str)
        or not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or type(detail.get("start")) is not int
        or type(detail.get("end")) is not int
        or detail["start"] < 1
        or detail["end"] < detail["start"]
        or not _hex40(detail.get("commit"))
        or not _hex40(detail.get("blob"))
        or not _digest(detail.get("doc_digest"))
        or not isinstance(detail.get("claim"), str)
        or not detail["claim"]
    ):
        raise ValueError("implementation-claim-detail-invalid")
    return detail


def _hex40(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(c in "0123456789abcdef" for c in value[7:])


def project_claims_ledger(state: object, *, iteration: object, doc_digest: object, git: object) -> dict:
    """Project fresh claims only; unavailable Git observations make claims stale."""
    if type(iteration) is not int or iteration < 1 or not _digest(doc_digest):
        raise ValueError("claims-ledger-input-invalid")
    try:
        head = git.head_commit()
    except Exception:  # Git absence is a stale observation, never a pass.
        head = None
    if not _hex40(head):
        head = None
    fresh: dict[tuple[str, str, str], list[tuple[bool, dict]]] = {}
    stale_count = 0
    history = state.get("verification_history") if isinstance(state, Mapping) else None
    for entry in history if isinstance(history, list) else ():
        checks = entry.get("checks") if isinstance(entry, Mapping) else None
        for check in checks if isinstance(checks, list) else ():
            if not isinstance(check, Mapping) or not isinstance(check.get("name"), str) or not check["name"].startswith(CLAIM_PREFIX):
                continue
            claim_id = check["name"][len(CLAIM_PREFIX):]
            try:
                detail = parse_claim_detail(check.get("detail"))
            except ValueError:
                stale_count += 1
                continue
            if not claim_id or type(check.get("ok")) is not bool or detail["doc_digest"] != doc_digest or detail["commit"] != head:
                stale_count += 1
                continue
            try:
                actual_blob = git.blob_at(detail["commit"], detail["path"])
            except Exception:
                actual_blob = None
            if actual_blob != detail["blob"]:
                stale_count += 1
                continue
            fresh.setdefault((claim_id, detail["doc_digest"], detail["commit"]), []).append((check["ok"], detail))
    entries = []
    for (claim_id, _digest_value, _commit), records in fresh.items():
        outcomes = {ok for ok, _detail_value in records}
        status = "conflicted" if len(outcomes) > 1 else "verified" if True in outcomes else "mismatch"
        entries.append({"claim_id": claim_id, "status": status, "revised": len(records) > 1, "detail": copy.deepcopy(records[-1][1])})
    entries.sort(key=lambda item: item["claim_id"])
    return {"schema": LEDGER_SCHEMA, "iteration": iteration, "doc_digest": doc_digest,
            "head_commit": head, "entries": entries, "stale_count": stale_count}


def unverified_claim_ids(
    ledger_path: object,
    state: object,
    *,
    iteration: object,
    doc_digest: object,
    head_commit: object,
    claim_ids: object,
) -> list[str]:
    """Return every expected claim that is not verified by a matching ledger."""
    expected = _claim_ids(claim_ids)
    try:
        content = Path(ledger_path).read_bytes()
        ledger = json.loads(content)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return expected
    if not _ledger_matches(
        ledger, state, content, iteration=iteration, doc_digest=doc_digest,
        head_commit=head_commit,
    ):
        return expected
    statuses = {
        entry.get("claim_id"): entry.get("status")
        for entry in ledger["entries"]
        if isinstance(entry, Mapping) and isinstance(entry.get("claim_id"), str)
    }
    candidates = expected + [claim_id for claim_id in statuses if claim_id not in expected]
    return [claim_id for claim_id in candidates if statuses.get(claim_id) != "verified"]


def _claim_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item for item in value):
        raise ValueError("claims-ledger-claim-ids-invalid")
    return list(dict.fromkeys(value))


def _ledger_matches(
    ledger: object,
    state: object,
    content: bytes,
    *,
    iteration: object,
    doc_digest: object,
    head_commit: object,
) -> bool:
    if not isinstance(ledger, Mapping) or not isinstance(state, Mapping):
        return False
    records = state.get("claims_ledgers")
    record = records.get(str(iteration)) if isinstance(records, Mapping) else None
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return (
        isinstance(record, Mapping)
        and record.get("digest") == digest
        and ledger.get("schema") == LEDGER_SCHEMA
        and ledger.get("iteration") == iteration
        and ledger.get("doc_digest") == doc_digest
        and ledger.get("head_commit") == head_commit
        and isinstance(ledger.get("entries"), list)
    )
