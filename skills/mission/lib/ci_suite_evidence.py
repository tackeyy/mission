"""Fail-closed validation for reusable CI shard evidence (#994)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable, Mapping, Optional


_SHA = re.compile(r"^[0-9a-f]{40}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s")
_ACTIONS_APP_ID = 15368
_RECEIPT_SCHEMA = "mission-test-report/1"


@dataclass(frozen=True)
class CiSuiteReuse:
    workflow_path: str
    shards: int
    target: str
    required_jobs: tuple[str, ...]


@dataclass(frozen=True)
class CiSuiteEvidence:
    accepted: bool
    reason: str
    run_id: Optional[int] = None
    tree_sha: Optional[str] = None
    job_ids: tuple[int, ...] = ()
    attempt: Optional[int] = None
    checkout_sha: Optional[str] = None


def parse_ci_reuse_contract(document: object) -> Optional[CiSuiteReuse]:
    """Return an opt-in only when every field is narrowly usable."""
    if not isinstance(document, Mapping):
        return None
    value = document.get("ci_reuse")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    workflow_path = value.get("workflow_path")
    shards = value.get("shards")
    target = value.get("target")
    required = value.get("required_jobs")
    if (not isinstance(workflow_path, str) or not workflow_path.startswith(".github/workflows/")
            or type(shards) is not int or shards <= 0
            or not isinstance(target, str) or not target
            or not isinstance(required, list) or not required
            or not all(isinstance(item, str) and item for item in required)):
        return None
    if len(set(required)) != len(required):
        return None
    return CiSuiteReuse(workflow_path, shards, target, tuple(required))


def reusable_tree_inputs_unchanged(changed_files: object, *, workflow_path: str) -> bool:
    """CI is evidence only while its runner, scope, and shard inputs are pinned."""
    if not isinstance(changed_files, (list, tuple, set)):
        return False
    protected = {
        workflow_path, "Makefile", "scripts/ci_changed_scopes.js",
        "scripts/ci_shard_targets.py", ".github/requirements-ci.txt",
    }
    return all(isinstance(path, str) and path not in protected for path in changed_files)


def _mapping(value: object) -> Optional[Mapping]:
    return value if isinstance(value, Mapping) else None


def _normalise_log(log: object) -> Optional[str]:
    if not isinstance(log, str):
        return None
    return "\n".join(_LOG_TIMESTAMP.sub("", line) for line in _ANSI.sub("", log).replace("\r", "").splitlines())


def _checkout_sha(log: object) -> Optional[str]:
    text = _normalise_log(log)
    if text is None:
        return None
    match = re.search(r"git log -1 --format=%H\n([0-9a-f]{40})(?:\n|$)", text)
    return match.group(1) if match else None


def _receipt(log: object, *, tree_sha: str, shard: int, total: int,
             target: str) -> bool:
    if not isinstance(log, str):
        return False
    text = _normalise_log(log)
    if text is None:
        return False
    command = ('make test-shard PYTHON=python SHARD_INDEX={} SHARD_TOTAL={} '
               'PYTEST_TARGETS="{}"').format(shard, total, target)
    if len(re.findall(r"(?:^|\n).*\bRun " + re.escape(command) + r"(?:\n|$)", text)) != 1:
        return False
    matches = []
    for line in text.splitlines():
        try:
            candidate = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(candidate, Mapping) and candidate.get("schema") == _RECEIPT_SCHEMA:
            matches.append(candidate)
    return len(matches) == 1 and matches[0] == {
        "schema": _RECEIPT_SCHEMA,
        "tree_sha": tree_sha,
        "tier": "shard",
        "shard": "{}/{}".format(shard, total),
        "test_manifest": [target],
    }


def verify_ci_suite(
    contract_document: object,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    integrated_tree_sha: str,
    api: Callable[[str], object],
) -> CiSuiteEvidence:
    """Validate a completed Actions run, otherwise describe the local fallback."""
    contract = parse_ci_reuse_contract(contract_document)
    if (contract is None or not isinstance(contract_document, Mapping)
            or contract_document.get("full_suite_command") != ["make", "test"]):
        return CiSuiteEvidence(False, "ci-reuse-unconfigured")
    if not all(isinstance(value, str) and _SHA.fullmatch(value)
               for value in (head_sha, base_sha, integrated_tree_sha)):
        return CiSuiteEvidence(False, "invalid-gate-sha")
    try:
        runs = _mapping(api("runs"))
        candidates = runs.get("workflow_runs") if runs else None
        if not isinstance(candidates, list) or not candidates:
            return CiSuiteEvidence(False, "run-unavailable")
        run = _mapping(candidates[0])
        if not run or any(run.get(key) != value for key, value in (
            ("head_sha", head_sha), ("event", "pull_request"),
            ("path", contract.workflow_path), ("status", "completed"),
            ("conclusion", "success"),
        )) or type(run.get("id")) is not int or type(run.get("run_attempt")) is not int \
                or type(run.get("check_suite_id")) is not int \
                or _mapping(run.get("repository")).get("full_name") != repository \
                or not any(isinstance(pr, Mapping) and pr.get("number") == pr_number
                           and pr.get("head", {}).get("sha") == head_sha
                           for pr in run.get("pull_requests", ())):
            return CiSuiteEvidence(False, "run-mismatch")
        run_id = run["id"]
        checks = _mapping(api("check"))
        suites = checks.get("check_suites") if checks else None
        if not isinstance(suites, list) or not any(
            isinstance(item, Mapping) and item.get("id") == run["check_suite_id"]
            and item.get("head_sha") == head_sha and item.get("status") == "completed"
            and item.get("conclusion") == "success" and _mapping(item.get("app"))
            and item["app"].get("id") == _ACTIONS_APP_ID and item["app"].get("slug") == "github-actions"
            for item in suites
        ):
            return CiSuiteEvidence(False, "check-suite-mismatch")
        jobs_payload = _mapping(api("jobs"))
        jobs = jobs_payload.get("jobs") if jobs_payload else None
        if (not isinstance(jobs, list) or jobs_payload.get("total_count") != len(jobs)
                or len(jobs) != len(contract.required_jobs) + contract.shards):
            return CiSuiteEvidence(False, "jobs-incomplete")
        job_ids = [job.get("id") for job in jobs if isinstance(job, Mapping)]
        if (len(job_ids) != len(jobs) or not all(type(job_id) is int and job_id > 0 for job_id in job_ids)
                or len(set(job_ids)) != len(job_ids)):
            return CiSuiteEvidence(False, "job-id-mismatch")
        named = {job.get("name"): job for job in jobs if isinstance(job, Mapping)}
        if len(named) != len(jobs) or any(
            name not in named or named[name].get("status") != "completed"
            or named[name].get("conclusion") != "success"
            or named[name].get("run_id") != run_id
            or named[name].get("run_attempt") != run["run_attempt"]
            or named[name].get("head_sha") != head_sha
            for name in contract.required_jobs
        ):
            return CiSuiteEvidence(False, "required-job-mismatch")
        ids = []
        checkouts = []
        logs = _mapping(api("logs"))
        for index in range(1, contract.shards + 1):
            job = named.get("Test {}".format(index))
            steps = job.get("steps") if isinstance(job, Mapping) else None
            if (not job or type(job.get("id")) is not int or job.get("status") != "completed"
                    or job.get("conclusion") != "success" or job.get("run_id") != run_id
                    or job.get("run_attempt") != run["run_attempt"] or job.get("head_sha") != head_sha
                    or not isinstance(steps, list) or any(not any(
                        isinstance(step, Mapping) and step.get("name") == name
                        and step.get("status") == "completed" and step.get("conclusion") == "success"
                        for step in steps
                    ) for name in ("Checkout", "Run Python tests"))
                    or not _receipt(logs.get(job["id"]) if logs else None,
                                      tree_sha=integrated_tree_sha, shard=index,
                                      total=contract.shards, target=contract.target)):
                return CiSuiteEvidence(False, "receipt-mismatch")
            ids.append(job["id"])
            checkouts.append(_checkout_sha(logs.get(job["id"]) if logs else None))
        if len(set(checkouts)) != 1 or checkouts[0] is None:
            return CiSuiteEvidence(False, "checkout-mismatch")
        merge_sha = checkouts[0]
        commit = _mapping(api("commit:" + merge_sha))
        parents = commit.get("parents") if commit else None
        if not commit or commit.get("tree", {}).get("sha") != integrated_tree_sha or commit.get("sha") != merge_sha:
            return CiSuiteEvidence(False, "merge-tree-mismatch")
        if (not isinstance(parents, list)
                or [parent.get("sha") for parent in parents if isinstance(parent, Mapping)] != [base_sha, head_sha]):
            return CiSuiteEvidence(False, "merge-parent-mismatch")
        final = _mapping(api("runs"))
        final_runs = final.get("workflow_runs") if final else None
        if (not isinstance(final_runs, list) or not final_runs
                or any(final_runs[0].get(key) != run.get(key) for key in
                       ("id", "head_sha", "status", "conclusion", "path", "event", "run_attempt"))):
            return CiSuiteEvidence(False, "run-changed")
        return CiSuiteEvidence(True, "ci-reuse-accepted", run_id, integrated_tree_sha, tuple(ids),
                               attempt=run["run_attempt"], checkout_sha=merge_sha)
    except Exception:
        return CiSuiteEvidence(False, "ci-evidence-unavailable")
