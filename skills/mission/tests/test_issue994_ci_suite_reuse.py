"""Issue #994: accept CI shards only when they prove the integrated tree."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import ci_suite_evidence as evidence  # noqa: E402
import integration_gate as gate  # noqa: E402


SHA = "a" * 40
BASE = "b" * 40
TREE = "c" * 40
MERGE = "d" * 40


def _contract():
    return {"schema": "mission-suite-contract/1", "full_suite_command": ["make", "test"], "ci_reuse": {
        "workflow_path": ".github/workflows/ci.yml", "shards": 2,
        "target": "skills/mission", "required_jobs": ["Shell", "Quality"],
    }}


def _responses():
    run = {"id": 7, "head_sha": SHA, "event": "pull_request", "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success", "run_attempt": 1, "check_suite_id": 8, "repository": {"full_name": "owner/repo"}, "pull_requests": [{"number": 9, "head": {"sha": SHA}}]}
    check = {"id": 8, "head_sha": SHA, "status": "completed", "conclusion": "success", "app": {"id": 15368, "slug": "github-actions"}}
    commit = {"sha": MERGE, "tree": {"sha": TREE}, "parents": [{"sha": BASE}, {"sha": SHA}]}
    jobs = {"total_count": 4, "jobs": [
        {"id": 1, "name": "Shell", "status": "completed", "conclusion": "success", "run_id": 7, "run_attempt": 1, "head_sha": SHA, "steps": []},
        {"id": 2, "name": "Quality", "status": "completed", "conclusion": "success", "run_id": 7, "run_attempt": 1, "head_sha": SHA, "steps": []},
        {"id": 3, "name": "Test 1", "status": "completed", "conclusion": "success", "run_id": 7, "run_attempt": 1, "head_sha": SHA, "steps": [{"name": "Checkout", "status": "completed", "conclusion": "success"}, {"name": "Run Python tests", "status": "completed", "conclusion": "success"}]},
        {"id": 4, "name": "Test 2", "status": "completed", "conclusion": "success", "run_id": 7, "run_attempt": 1, "head_sha": SHA, "steps": [{"name": "Checkout", "status": "completed", "conclusion": "success"}, {"name": "Run Python tests", "status": "completed", "conclusion": "success"}]},
    ]}
    receipt = '{{"schema":"mission-test-report/1","tree_sha":"{}","tier":"shard","shard":"{}/2","test_manifest":["skills/mission"]}}'
    return {
        "runs": {"workflow_runs": [run]},
        "check": {"check_suites": [check]},
        "commit:" + MERGE: commit,
        "jobs": jobs,
        "logs": {3: "git log -1 --format=%H\n{}\n2026-01-01Z ##[group]Run make test-shard PYTHON=python SHARD_INDEX=1 SHARD_TOTAL=2 PYTEST_TARGETS=\"skills/mission\"\n".format(MERGE) + receipt.format(TREE, 1), 4: "git log -1 --format=%H\n{}\n2026-01-01Z ##[group]Run make test-shard PYTHON=python SHARD_INDEX=2 SHARD_TOTAL=2 PYTEST_TARGETS=\"skills/mission\"\n".format(MERGE) + receipt.format(TREE, 2)},
    }


def test_matching_ci_receipts_accept_the_integrated_tree():
    data = _responses()
    result = evidence.verify_ci_suite(
        _contract(), repository="owner/repo", pr_number=9, head_sha=SHA,
        base_sha=BASE, integrated_tree_sha=TREE,
        api=lambda endpoint: data[endpoint],
    )

    assert result.accepted is True
    assert result.run_id == 7
    assert result.tree_sha == TREE


def test_a_missing_shard_receipt_falls_back_without_accepting_ci():
    data = _responses()
    data["logs"][4] = data["logs"][4].replace('"shard":"2/2",', '"shard":"1/2",')
    result = evidence.verify_ci_suite(
        _contract(), repository="owner/repo", pr_number=9, head_sha=SHA,
        base_sha=BASE, integrated_tree_sha=TREE,
        api=lambda endpoint: data[endpoint],
    )

    assert result.accepted is False
    assert result.reason == "receipt-mismatch"


def test_an_invalid_or_absent_opt_in_contract_cannot_enable_reuse():
    assert evidence.parse_ci_reuse_contract({}) is None
    assert evidence.parse_ci_reuse_contract({"ci_reuse": {"shards": 0}}) is None


def test_an_api_exception_is_a_local_suite_fallback():
    result = evidence.verify_ci_suite(
        _contract(), repository="owner/repo", pr_number=9, head_sha=SHA,
        base_sha=BASE, integrated_tree_sha=TREE,
        api=lambda _endpoint: (_ for _ in ()).throw(AttributeError("transport")),
    )
    assert result.accepted is False
    assert result.reason == "ci-evidence-unavailable"


def test_ci_runner_inputs_changed_by_the_pr_force_local_execution():
    kwargs = {"workflow_path": ".github/workflows/ci.yml"}
    assert evidence.reusable_tree_inputs_unchanged(["skills/mission/lib/x.py"], **kwargs)
    assert not evidence.reusable_tree_inputs_unchanged(["Makefile"], **kwargs)


def test_reuse_requires_the_base_make_test_suite_contract():
    contract = _contract()
    contract["full_suite_command"] = ["python", "alternate.py"]
    data = _responses()
    result = evidence.verify_ci_suite(contract, repository="owner/repo", pr_number=9,
                                      head_sha=SHA, base_sha=BASE, integrated_tree_sha=TREE,
                                      api=lambda endpoint: data[endpoint])
    assert result.reason == "ci-reuse-unconfigured"


def test_boolean_sha_and_changed_configured_workflow_fall_back():
    data = _responses()
    assert evidence.verify_ci_suite(_contract(), repository="owner/repo", pr_number=9,
                                    head_sha=True, base_sha=BASE, integrated_tree_sha=TREE,
                                    api=lambda endpoint: data[endpoint]).accepted is False
    assert not evidence.reusable_tree_inputs_unchanged(
        [".github/workflows/custom-ci.yml"], workflow_path=".github/workflows/custom-ci.yml")


def test_duplicate_or_invalid_job_ids_and_missing_checkout_are_rejected():
    for mutation in (
        lambda data: data["jobs"]["jobs"].__setitem__(1, {**data["jobs"]["jobs"][1], "id": 1}),
        lambda data: data["jobs"]["jobs"].__setitem__(2, {**data["jobs"]["jobs"][2], "id": 0}),
        lambda data: data["jobs"]["jobs"][2].update({"steps": [{"name": "Run Python tests", "status": "completed", "conclusion": "success"}]}),
    ):
        data = _responses()
        mutation(data)
        assert not evidence.verify_ci_suite(_contract(), repository="owner/repo", pr_number=9,
                                            head_sha=SHA, base_sha=BASE, integrated_tree_sha=TREE,
                                            api=lambda endpoint: data[endpoint]).accepted


def test_actions_api_uses_the_origin_repository_without_its_host(tmp_path):
    """The gate identity includes a host; GitHub REST paths must not."""
    seen = []

    def runner(arguments, _cwd, env=None):
        seen.append(arguments)
        return gate.CommandResult(0, '{"workflow_runs": []}')

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    assert operations._ci_api("runs", repository="github.com/owner/repo",
                              contract=evidence.parse_ci_reuse_contract(_contract()), head_sha=SHA) == {"workflow_runs": []}
    assert seen[0][-1].startswith("repos/owner/repo/")


def _git(repo, *arguments):
    return subprocess.run(("git", *arguments), cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


def _integration_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / ".mission").mkdir()
    (repo / "scripts").mkdir()
    shutil.copy(ROOT.parents[1] / "scripts" / "ci_changed_scopes.js", repo / "scripts" / "ci_changed_scopes.js")
    (repo / ".mission" / "suite-contract.json").write_text(json.dumps(_contract()), encoding="utf-8")
    (repo / "Makefile").write_text("test:\n\t@true\n", encoding="utf-8")
    (repo / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    base = _git(repo, "commit", "-qm", "base") or _git(repo, "rev-parse", "HEAD")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "feature")
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "module.py")
    _git(repo, "commit", "-qm", "head")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _ci_api_for(repo, base, head, *, wrong_tree=False):
    tree = _git(repo, "rev-parse", head + "^{tree}")
    merge = _git(repo, "commit-tree", tree, "-p", base, "-p", head, "-m", "CI merge")
    data = _responses()
    data["runs"]["workflow_runs"][0]["head_sha"] = head
    data["runs"]["workflow_runs"][0]["pull_requests"][0]["head"]["sha"] = head
    data["check"]["check_suites"][0]["head_sha"] = head
    for job in data["jobs"]["jobs"]:
        job["head_sha"] = head
    data["commit:" + MERGE] = data.pop("commit:" + MERGE)
    data["commit:" + merge] = {"sha": merge, "tree": {"sha": "0" * 40 if wrong_tree else tree}, "parents": [{"sha": base}, {"sha": head}]}
    for job_id, shard in ((3, 1), (4, 2)):
        data["logs"][job_id] = "git log -1 --format=%H\n{}\nRun make test-shard PYTHON=python SHARD_INDEX={} SHARD_TOTAL=2 PYTEST_TARGETS=\"skills/mission\"\n{{\"schema\":\"mission-test-report/1\",\"tree_sha\":\"{}\",\"tier\":\"shard\",\"shard\":\"{}/2\",\"test_manifest\":[\"skills/mission\"]}}".format(merge, shard, tree, shard)
    return data


@pytest.mark.parametrize("wrong_tree,expect_suite", [(False, False), (True, True)])
def test_integrate_and_test_uses_real_ci_verifier_to_skip_or_fallback(tmp_path, wrong_tree, expect_suite):
    repo, base, head = _integration_repo(tmp_path)
    api = _ci_api_for(repo, base, head, wrong_tree=wrong_tree)
    suite_calls = []

    def runner(arguments, cwd, env=None):
        if tuple(arguments) == ("make", "test"):
            suite_calls.append(tuple(arguments))
            Path(env[gate.SUITE_REPORT_ENV]).write_text(json.dumps({"schema": "mission-suite-report/1", "tree_sha": _git(cwd, "write-tree"), "executed": 1, "status": "complete"}), encoding="utf-8")
            return gate.CommandResult(0)
        return gate._local_runner(arguments, cwd, env)

    operations = gate.SubprocessGateOperations(repo, runner=runner)
    operations._active_pr_number = 9
    operations._identity = lambda: "github.com/owner/repo"
    operations._ci_api = lambda kind, **_kwargs: api.get(kind)
    operations.integrate_and_test(head, base, lambda _line: None)
    assert bool(suite_calls) is expect_suite
