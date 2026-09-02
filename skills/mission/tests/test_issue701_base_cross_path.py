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

import json
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
MOVED_BASE_SHA = "9" * 40
FAKE_DIGEST = "f" * 64


def _snapshot(state="OPEN", *, base_ref_oid=BASE_SHA, merged_at=None, merge_sha=None):
    return gate.PullRequestSnapshot(
        1, HEAD_SHA, "main", state, merged_at, merge_sha, base_ref_oid
    )


class RecordingOperations:
    """A fake where fetching is what makes a new base observable.

    The earlier version handed out base shas from an iterator regardless of
    whether ``fetch_base`` had been called, so removing the implementation's
    re-fetch changed nothing the tests could see.  Modelling the remote and the
    fetched ref separately is what makes the re-fetch observable: without it,
    ``current_base_sha`` keeps returning the stale value.
    """

    def __init__(self, *, remote_schedule=None, snapshots=None):
        # Values the remote takes on successive fetches.  The last one repeats.
        self.remote_schedule = list(remote_schedule or [BASE_SHA])
        self.fetched_base = None
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
        self.fetched_base = (
            self.remote_schedule.pop(0)
            if len(self.remote_schedule) > 1
            else self.remote_schedule[0]
        )

    def current_base_sha(self):
        if self.fetched_base is None:
            raise AssertionError("current_base_sha read before any fetch_base")
        self.calls.append(("current_base_sha", self.fetched_base))
        return self.fetched_base

    def read_pull_request(self, pr_ref, step=6):
        return self.snapshots.pop(0)

    def fetch_pull_request_head(self, snapshot):
        pass

    def integrate_and_test(self, head_sha, base_sha, logger):
        self.calls.append(("integrate_and_test", head_sha, base_sha))
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
    """git reaching another repository shows up as a base the API does not report.

    The re-fetch shows git's own view is stable, so this is a rewrite rather
    than the base moving.
    """
    operations = RecordingOperations(remote_schedule=[REWRITTEN_BASE_SHA])

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.step == 3
    assert excinfo.value.reason == "base-observation-disagrees"
    # The check has to land before the suite runs; otherwise the gate spends a
    # full suite on a tree fetched from somewhere it did not verify.
    assert not any(call[0] == "integrate_and_test" for call in operations.calls)
    assert not any(call[0] == "merge" for call in operations.calls)


def test_a_base_that_actually_moved_keeps_its_existing_failure_reason():
    """Movement is not a rewrite, and must not be reported as one.

    Reporting a moved base as a resolution problem would send the operator
    after a security question that is not there.
    """
    operations = RecordingOperations(
        remote_schedule=[BASE_SHA, MOVED_BASE_SHA],
        snapshots=[
            _snapshot(base_ref_oid=MOVED_BASE_SHA),
            _snapshot(base_ref_oid=MOVED_BASE_SHA),
            _snapshot("MERGED", merged_at="2026-09-02T00:00:00Z", merge_sha=MERGE_SHA),
        ],
    )

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.reason == "base-moved"
    assert not any(call[0] == "merge" for call in operations.calls)
    # The verdict must come from a fresh observation, not the one already held.
    # Without the re-fetch the stale value still matches and this reports a
    # rewriting problem instead.
    kinds = [call[0] for call in operations.calls]
    assert kinds.count("fetch_base") >= 2
    assert kinds.index("current_base_sha") < len(kinds) - 1


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
    """An observation we cannot compare is not an observation; never merge on it.

    It carries its own reason so that "the check could not run" stays
    distinguishable from "the check ran and disagreed".
    """
    operations = RecordingOperations(snapshots=[
        _snapshot(base_ref_oid=value),
        _snapshot(),
        _snapshot("MERGED", merged_at="2026-09-02T00:00:00Z", merge_sha=MERGE_SHA),
    ])

    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)

    assert excinfo.value.step == 3
    assert excinfo.value.reason == "base-observation-unusable"
    assert not any(call[0] == "integrate_and_test" for call in operations.calls)
    assert not any(call[0] == "merge" for call in operations.calls)


def test_the_adapter_asks_the_api_for_the_base_it_compares():
    """Without this, dropping baseRefOid from the query changes nothing visible.

    The application layer only sees the snapshot, so a query that never
    requests the field would leave every application-level test passing while
    the comparison silently had nothing to compare.
    """
    recorded = []

    def runner(arguments, cwd):
        arguments = tuple(arguments)
        recorded.append(arguments)
        if "config" in arguments or "remote" in arguments:
            return gate.CommandResult(0, "https://github.com/acme/widgets.git", "")
        payload = json.dumps({
            "number": 1,
            "headRefOid": HEAD_SHA,
            "baseRefName": "main",
            "baseRefOid": BASE_SHA,
            "state": "OPEN",
            "mergedAt": None,
            "mergeCommit": None,
        })
        return gate.CommandResult(0, payload, "")

    operations = gate.SubprocessGateOperations(Path("."), runner=runner)
    snapshot = operations.read_pull_request("1", step=3)

    query = next(args for args in recorded if "--json" in args)
    fields = query[query.index("--json") + 1].split(",")
    assert "baseRefOid" in fields
    assert snapshot.base_ref_oid == BASE_SHA
