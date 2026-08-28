"""Issue #681: implementation claim grammar and ledger projections."""

from __future__ import annotations

import hashlib
import json

import pytest


SHA = "a" * 40
BLOB = "b" * 40
DIGEST = "sha256:" + "c" * 64


def _detail(**overrides):
    value = {
        "repo": "self", "path": "skills/mission/SKILL.md", "start": 1, "end": 2,
        "commit": SHA, "blob": BLOB, "doc_digest": DIGEST, "claim": "documented behavior",
    }
    value.update(overrides)
    return json.dumps(value)


@pytest.mark.parametrize(
    "detail",
    [
        "not json",
        _detail(repo="other"),
        _detail(path="/absolute"),
        _detail(path="dir/../file"),
        _detail(start=0),
        _detail(end=0),
        _detail(start=3, end=2),
        _detail(commit="a" * 39),
        _detail(blob="b" * 39),
        _detail(doc_digest="sha256:" + "c" * 63),
        _detail(claim=""),
        _detail(extra="rejected"),
    ],
)
def test_implementation_claim_grammar_rejects_each_invalid_form(detail):
    from mission_application.evidence import EvidenceFailure, normalize_verification_payload

    with pytest.raises(EvidenceFailure, match="implementation-claim-detail-invalid"):
        normalize_verification_payload({"kind": "implementation-read", "checks": [{
            "name": "implementation-verified:claim-1", "ok": True, "detail": detail,
        }]})


def test_non_claim_check_keeps_free_form_detail():
    from mission_application.evidence import normalize_verification_payload

    kind, checks = normalize_verification_payload({"checks": [{
        "name": "tests", "ok": True, "detail": "any human detail",
    }]})

    assert kind == "execution"
    assert checks[0].detail == "any human detail"


def test_implementation_claim_requires_a_claim_identifier():
    from mission_application.evidence import EvidenceFailure, normalize_verification_payload

    with pytest.raises(EvidenceFailure, match="implementation-claim-name-invalid"):
        normalize_verification_payload({"kind": "implementation-read", "checks": [{
            "name": "implementation-verified:", "ok": True, "detail": _detail(),
        }]})


class _Git:
    def __init__(self, head=SHA, blob=BLOB):
        self.head = head
        self.blob = blob

    def head_commit(self):
        return self.head

    def blob_at(self, commit, path):
        return self.blob


def _state(*checks):
    return {"verification_history": [{"checks": list(checks)}]}


def _check(claim_id="claim-1", *, ok=True, detail=None):
    return {"name": "implementation-verified:" + claim_id, "ok": ok,
            "detail": detail or _detail()}


def test_claim_ledger_classifies_stale_conflicted_mismatch_verified_and_revised():
    from mission_application.claims_ledger import project_claims_ledger

    stale = _check("stale", detail=_detail(commit="d" * 40))
    conflicted_true = _check("conflicted", ok=True)
    conflicted_false = _check("conflicted", ok=False)
    mismatch = _check("mismatch", ok=False)
    revised_first = _check("revised", ok=True)
    revised_last = _check("revised", ok=True)
    verified = _check("verified", ok=True)

    ledger = project_claims_ledger(
        _state(stale, conflicted_true, conflicted_false, mismatch, revised_first, revised_last, verified),
        iteration=3, doc_digest=DIGEST, git=_Git(),
    )

    assert ledger["stale_count"] == 1
    entries = {entry["claim_id"]: entry for entry in ledger["entries"]}
    assert {key: entry["status"] for key, entry in entries.items()} == {
        "conflicted": "conflicted", "mismatch": "mismatch", "revised": "verified", "verified": "verified",
    }
    assert entries["revised"]["revised"] is True
    assert all("ok" not in entry for entry in ledger["entries"])


def test_claim_ledger_treats_blob_mismatch_as_stale():
    from mission_application.claims_ledger import project_claims_ledger

    ledger = project_claims_ledger(
        _state(_check()), iteration=1, doc_digest=DIGEST, git=_Git(blob="d" * 40),
    )

    assert ledger["entries"] == []
    assert ledger["stale_count"] == 1


def test_claims_ledger_cli_writes_digest_to_state(legacy_run_cli, read_state, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "claim.txt"
    target.write_text("claim evidence\n")
    assert legacy_run_cli("init", "claim ledger", cwd=repo).returncode == 0
    assert legacy_run_cli("verification", "record", "--iteration", "1", "--stdin", cwd=repo,
                   input_text=json.dumps({"kind": "implementation-read", "checks": [{
                       "name": "implementation-verified:claim-1", "ok": True,
                       "detail": _detail(path="claim.txt"),
                   }]})).returncode == 0
    result = legacy_run_cli("verification", "claims", "--iteration", "1", "--doc-digest", DIGEST,
                     "--out", "ledger.json", cwd=repo)

    assert result.returncode == 0, result.stderr + result.stdout
    ledger = json.loads((repo / "ledger.json").read_text())
    state = read_state(repo / ".mission-state")
    record = state["claims_ledgers"]["1"]
    assert record["digest"] == "sha256:" + hashlib.sha256((repo / "ledger.json").read_bytes()).hexdigest()
    assert ledger["schema"] == "mission-claims-ledger/1"
