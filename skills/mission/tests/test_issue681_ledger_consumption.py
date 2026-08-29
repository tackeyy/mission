"""Issue #681: fail-closed consumption of implementation claims ledgers."""

from __future__ import annotations

import hashlib
import json

import pytest


ITERATION = 3
DOC_DIGEST = "sha256:" + "d" * 64
HEAD_COMMIT = "a" * 40
CLAIM_IDS = ("claim-verified", "claim-mismatch", "claim-conflicted", "claim-missing")


def _ledger(**overrides):
    value = {
        "schema": "mission-claims-ledger/1",
        "iteration": ITERATION,
        "doc_digest": DOC_DIGEST,
        "head_commit": HEAD_COMMIT,
        "entries": [
            {"claim_id": "claim-verified", "status": "verified"},
            {"claim_id": "claim-mismatch", "status": "mismatch"},
            {"claim_id": "claim-conflicted", "status": "conflicted"},
        ],
        "stale_count": 0,
    }
    value.update(overrides)
    return value


def _write_ledger(tmp_path, ledger):
    path = tmp_path / "claims-ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return path


def _state(path):
    return {
        "claims_ledgers": {
            str(ITERATION): {
                "path": str(path),
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "doc_digest": DOC_DIGEST,
            }
        }
    }


def _consume(path, state):
    from mission_application.claims_ledger import unverified_claim_ids

    return unverified_claim_ids(
        path, state, iteration=ITERATION, doc_digest=DOC_DIGEST,
        head_commit=HEAD_COMMIT, claim_ids=CLAIM_IDS,
    )


@pytest.mark.parametrize(
    "ledger_override,state_override",
    [
        ({}, {"digest": "sha256:" + "0" * 64}),
        ({"schema": "other/1"}, {}),
        ({"iteration": ITERATION + 1}, {}),
        ({"doc_digest": "sha256:" + "e" * 64}, {}),
        ({"head_commit": "b" * 40}, {}),
    ],
    ids=["digest-mismatch", "schema-mismatch", "iteration-mismatch", "doc-digest-mismatch", "head-commit-mismatch"],
)
def test_invalid_ledger_identity_returns_every_expected_claim_as_unverified(tmp_path, ledger_override, state_override):
    path = _write_ledger(tmp_path, _ledger(**ledger_override))
    state = _state(path)
    state["claims_ledgers"][str(ITERATION)].update(state_override)

    assert _consume(path, state) == list(CLAIM_IDS)


def test_unreadable_ledger_returns_every_expected_claim_as_unverified(tmp_path):
    path = tmp_path / "claims-ledger.json"
    published = _write_ledger(tmp_path, _ledger())
    state = _state(published)
    published.unlink()

    assert _consume(path, state) == list(CLAIM_IDS)


def test_valid_ledger_excludes_only_verified_claims(tmp_path):
    path = _write_ledger(tmp_path, _ledger())

    assert _consume(path, _state(path)) == [
        "claim-mismatch", "claim-conflicted", "claim-missing",
    ]
