"""Issue #681 AC3: the pass gate keeps its meaning when a claims ledger exists.

The claims ledger (#681) records which implementation ranges a reviewer read.
It is an input to review quality, not to the gate.  These tests pin that
separation so a later change cannot quietly route ledger state into the gate.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


SHA = "a" * 40
BLOB = "b" * 40
DIGEST = "sha256:" + "c" * 64

PASSING_ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}
FAILING_ITEMS = {
    "mission_achievement": 3.4,
    "accuracy": 3.3,
    "completeness": 3.2,
    "usability": 3.1,
}


def _claim_detail(path: str) -> str:
    return json.dumps({
        "repo": "self", "path": path, "start": 1, "end": 2,
        "commit": SHA, "blob": BLOB, "doc_digest": DIGEST,
        "claim": "documented behavior",
    })


def _record_claims_ledger(repo, legacy_run_cli) -> None:
    """Put one verified claim and its ledger into the state under test."""
    target = repo / "claim.txt"
    target.write_text("claim evidence\n")
    assert legacy_run_cli(
        "verification", "record", "--iteration", "1", "--stdin", cwd=repo,
        input_text=json.dumps({"kind": "implementation-read", "checks": [{
            "name": "implementation-verified:claim-1", "ok": True,
            "detail": _claim_detail("claim.txt"),
        }]}),
    ).returncode == 0
    result = legacy_run_cli(
        "verification", "claims", "--iteration", "1", "--doc-digest", DIGEST,
        "--out", "ledger.json", cwd=repo,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def _push_score(repo, legacy_run_cli, items, *, high_count=0) -> None:
    evidence_path, ref, claim = write_canonical_review_aggregate(
        repo,
        [canonical_review(items, perspective="A", high_count=high_count)],
        name_prefix="pass-gate-invariance",
    )
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": str(evidence_path),
        "notes": "pass gate invariance fixture",
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }
    scoring = repo / "scoring.json"
    scoring.write_text(json.dumps(payload), encoding="utf-8")
    assert legacy_run_cli(
        "push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=repo,
    ).returncode == 0


def _prepare(repo, legacy_run_cli, *, with_ledger: bool, items, high_count=0) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    assert legacy_run_cli("init", "pass gate invariance", cwd=repo).returncode == 0
    # The artifact contract is a separate gate input; resolve it so the
    # comparison below isolates the claims ledger.
    assert legacy_run_cli(
        "advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable",
        cwd=repo,
    ).returncode == 0
    if with_ledger:
        _record_claims_ledger(repo, legacy_run_cli)
    _push_score(repo, legacy_run_cli, items, high_count=high_count)


@pytest.mark.parametrize("items,expected_returncode", [
    (PASSING_ITEMS, 0),
    (FAILING_ITEMS, 2),
])
def test_mark_passes_outcome_is_identical_with_and_without_a_claims_ledger(
    legacy_run_cli, read_state, tmp_path, items, expected_returncode,
):
    """The same score must decide the same way whether or not a ledger exists."""
    outcomes = {}
    for with_ledger in (False, True):
        repo = tmp_path / ("with-ledger" if with_ledger else "without-ledger")
        _prepare(repo, legacy_run_cli, with_ledger=with_ledger, items=items)
        result = legacy_run_cli("mark-passes", cwd=repo)
        state = read_state(repo / ".mission-state")
        outcomes[with_ledger] = (result.returncode, state["passes"], state.get("halt_reason", ""))
        # The ledger must actually be present in the arm that claims it, or the
        # comparison would pass for the wrong reason.
        assert bool(state.get("claims_ledgers")) is with_ledger

    assert outcomes[False][0] == expected_returncode
    assert outcomes[True] == outcomes[False]


def test_claims_ledger_is_not_part_of_the_gate_input(legacy_run_cli, read_state, tmp_path):
    """The recorded score must be byte-identical whether or not a ledger exists.

    Comparing whole key sets rather than a name pattern is deliberate: a leak
    named ``ledger_present`` or ``review_evidence_applied`` carries the same
    state without containing the substring "claim", so a pattern check would
    let it through.
    """
    recorded = {}
    for with_ledger in (False, True):
        repo = tmp_path / ("keys-with" if with_ledger else "keys-without")
        _prepare(repo, legacy_run_cli, with_ledger=with_ledger, items=PASSING_ITEMS)
        assert legacy_run_cli("mark-passes", cwd=repo).returncode == 0
        state = read_state(repo / ".mission-state")
        assert bool(state.get("claims_ledgers")) is with_ledger
        recorded[with_ledger] = state["score_history"][-1]

    # The key set must not gain a field, whatever it is named.
    assert set(recorded[True]) == set(recorded[False]), (
        "the recorded score gained or lost a field because a ledger existed"
    )
    for key in ("composite", "open_high", "min_item"):
        assert key in recorded[False], f"{key} is missing from the recorded score"

    # Compare every remaining value, not a chosen subset.  A leak that adds the
    # same key to both arms with different values passes a key-set check and a
    # subset check alike; only comparing what is left catches it.
    #
    # The exclusions below differ between the arms by construction, not because
    # of the ledger: each arm runs in its own directory and at its own moment.
    arm_specific = {
        "timestamp",                # wall clock
        "findings_evidence_path",   # embeds the per-arm directory
        "score_provenance",         # carries evidence paths and their digests
        "artifact_digest",          # digest of the per-arm artifact
        "artifact_digest_status",
        "scoring_evidence_path",     # embeds the per-arm directory
    }
    for key in set(recorded[False]) - arm_specific:
        assert recorded[True][key] == recorded[False][key], (
            f"the recorded value of {key} changed because a ledger existed"
        )


def test_ledger_digest_is_recorded_outside_the_score_history(legacy_run_cli, read_state, tmp_path):
    """The ledger lives in its own field so gate consumers never see it by accident."""
    repo = tmp_path / "ledger-placement"
    _prepare(repo, legacy_run_cli, with_ledger=True, items=PASSING_ITEMS)
    state = read_state(repo / ".mission-state")

    record = state["claims_ledgers"]["1"]
    expected = "sha256:" + hashlib.sha256((repo / "ledger.json").read_bytes()).hexdigest()
    assert record["digest"] == expected
    assert "claims_ledgers" not in state["score_history"][-1]


def test_open_high_gate_is_unchanged_by_a_claims_ledger(legacy_run_cli, read_state, tmp_path):
    """The open_high rejection path must also ignore the ledger.

    The other cases all score with ``high_count=0``, so they exercise the
    composite and min_item gates but never the open_high one.  A change that
    routed ledger state into the open_high check would pass all of them.
    """
    outcomes = {}
    for with_ledger in (False, True):
        repo = tmp_path / ("high-with" if with_ledger else "high-without")
        _prepare(
            repo, legacy_run_cli, with_ledger=with_ledger,
            items=PASSING_ITEMS, high_count=1,
        )
        result = legacy_run_cli("mark-passes", cwd=repo)
        state = read_state(repo / ".mission-state")
        assert bool(state.get("claims_ledgers")) is with_ledger
        outcomes[with_ledger] = (result.returncode, state["passes"])

    # An open High must reject regardless of the ledger.
    assert outcomes[False] == (2, False)
    assert outcomes[True] == outcomes[False]
