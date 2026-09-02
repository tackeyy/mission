"""Issue #701: the gate pins its base against GitHub, not against git config alone.

#698 moved git operations from the mutable remote name ``origin`` to a verified
URL.  That closed remote swapping, but ``url.<base>.insteadOf`` still rewrites
where git resolves a URL to, so ``ls-remote`` / ``fetch`` can reach repository B
while ``gh`` -- which uses the verified identity -- reads repository A.

``gh`` is not affected by git's rewriting rules, so the base branch tip it
reports is an independent observation of the same fact.  Requiring the two to
agree makes a rewrite visible: the git side would report the rewritten
repository's commit and the two would not match.

This does not add a new failure mode.  The gate already requires the base to
stay still for the whole run (``base-moved`` at step 5), so a legitimate move
between the two observations fails there regardless.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import integration_gate as gate  # noqa: E402
from mission_application import integration_gate as application  # noqa: E402

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
REWRITTEN_BASE_SHA = "e" * 40
FAKE_DIGEST = "f" * 64


def _snapshot(state="OPEN", *, base_ref_oid=BASE_SHA, merged_at=None, merge_sha=None):
    return gate.PullRequestSnapshot(
        1, HEAD_SHA, "main", state, merged_at, merge_sha, base_ref_oid
    )


class RecordingOperations:
    def __init__(self, *, base_shas=None, snapshots=None):
        self.base_shas = iter(base_shas or [BASE_SHA, BASE_SHA])
        self.calls = []
        self.snapshots = list(snapshots or [
            _snapshot(),
            _snapshot(),
            _snapshot("MERGED", merged_at="2026-09-02T00:00:00Z", merge_sha=MERGE_SHA),
        ])

    @contextmanager
    def lease(self):
        yield

    def resolve_origin_identity(self):
        return "github.com/acme/widgets"

    def resolve_default_branch(self):
        return "main"

    def fetch_base(self, branch):
        self.calls.append(("fetch_base", branch))

    def current_base_sha(self):
        return next(self.base_shas)

    def read_pull_request(self, pr_ref, step=6):
        return self.snapshots.pop(0)

    def fetch_pull_request_head(self, snapshot):
        pass

    def integrate_and_test(self, head_sha, base_sha, logger):
        return application.IntegrationObservation("full", "all", "d" * 40, FAKE_DIGEST)

    def merge_pull_request(self, pr_ref, expected_head_sha):
        self.calls.append(("merge", pr_ref, expected_head_sha))


def run(operations):
    return application.run_gate_and_merge(
        "1",
        application.GateRuntimeServices(
            lease=operations.lease,
            resolve_origin_identity=operations.resolve_origin_identity,
            resolve_default_branch=operations.resolve_default_branch,
            fetch_base=operations.fetch_base,
            current_base_sha=operations.current_base_sha,
            read_pull_request=operations.read_pull_request,
            fetch_pull_request_head=operations.fetch_pull_request_head,
            integrate_and_test=operations.integrate_and_test,
            merge_pull_request=operations.merge_pull_request,
        ),
        (lambda _m: None),
        expected_changeset_digest=FAKE_DIGEST,
    )


def test_agreeing_observations_complete_the_gate():
    """Without a rewrite the two paths agree, so nothing changes."""
    operations = RecordingOperations()

    assert run(operations)["status"] == "merged"


def test_rewritten_git_resolution_is_rejected_before_the_tests_run():
    """git reaching another repository shows up as a base the API does not report."""
    operations = RecordingOperations(
        base_shas=[REWRITTEN_BASE_SHA, REWRITTEN_BASE_SHA]
    )

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.step == 3
    assert excinfo.value.reason == "base-observation-disagrees"
    assert not any(call[0] == "merge" for call in operations.calls)


def test_disagreement_appearing_before_the_merge_is_rejected():
    """The window between the last check and the merge is covered too."""
    operations = RecordingOperations(snapshots=[
        _snapshot(),
        _snapshot(base_ref_oid=REWRITTEN_BASE_SHA),
        _snapshot("MERGED", merged_at="2026-09-02T00:00:00Z", merge_sha=MERGE_SHA),
    ])

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.step == 6
    assert excinfo.value.reason == "base-observation-disagrees"
    assert not any(call[0] == "merge" for call in operations.calls)


@pytest.mark.parametrize(
    "value", [None, "", "not-a-sha", "B" * 40, "b" * 39], ids=
    ["absent", "empty", "not-hex", "uppercase", "too-short"],
)
def test_an_unusable_api_observation_stops_the_gate(value):
    """An observation we cannot compare is not an observation; never merge on it."""
    operations = RecordingOperations(snapshots=[
        _snapshot(base_ref_oid=value),
        _snapshot(),
        _snapshot("MERGED", merged_at="2026-09-02T00:00:00Z", merge_sha=MERGE_SHA),
    ])

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.reason == "base-observation-disagrees"
    assert not any(call[0] == "merge" for call in operations.calls)
