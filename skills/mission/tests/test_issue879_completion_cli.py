"""Completion cannot turn pending acceptance evidence into a public success.

Valid coverage below is a persisted-state fixture with no public producer.
It proves the lower receipt/fresh-review guard is reachable, never a success
path. Actual command receipts still come from the public verification runner.
"""
from dataclasses import replace
from datetime import datetime, timezone
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import canonical_review, write_canonical_review_aggregate
from .test_issue878_candidate_snapshot import _commit_candidate
from .test_issue878_verification_runner import _contract, _policy, _replay_policy
from .test_issue503_fenced_commit import _request


def _public_bytes(root):
    """Compare published state/evidence, including backups and absent files.

    Lock files and recovery journals are repository coordination, not public
    state. Approval receipts are public evidence and are included.
    """
    state_root = root / ".mission-state"
    directories = {"sessions", "archive", "evidence", "objects", "generations", "commits", "operations"}
    return {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file() and (path.relative_to(state_root).parts[0] in directories
                               or path.parent == state_root and path.suffix == ".json")
    }


def _persisted_fixture_document(root):
    """Inspect published fixture bytes even when authoritative loads reject them."""
    state_root = root / '.mission-state'
    document = json.loads((state_root / 'sessions' / 'test.json').read_bytes())
    if document.get('schema') != 'mission-head/1':
        return document
    manifest = json.loads((state_root / document['state_generation']['path']).read_bytes())
    return json.loads((state_root / manifest['state']['object']).read_bytes())


def _rewrite_fixture_document(root, mutate):
    """Forge a coherent persisted fixture without admitting it through a writer."""
    state_root = root / '.mission-state'
    path = state_root / 'sessions' / 'test.json'
    head = json.loads(path.read_bytes())
    if head.get('schema') != 'mission-head/1':
        mutate(head)
        path.write_text(json.dumps(head))
        return
    manifest = json.loads((state_root / head['state_generation']['path']).read_bytes())
    document = _persisted_fixture_document(root)
    mutate(document)

    def publish(value, directory, suffix):
        payload = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(payload).hexdigest()
        relative = f'{directory}/{digest}{suffix}'
        (state_root / relative).write_bytes(payload)
        return {'digest': 'sha256:' + digest, 'path': relative, 'size': len(payload)}

    state_ref = publish(document, 'objects', '.blob')
    manifest['state'] = {'digest': state_ref['digest'], 'object': state_ref['path'], 'size': state_ref['size']}
    generation_ref = publish(manifest, 'generations', '.json')
    commit = json.loads((state_root / head['commit']['path']).read_bytes())
    commit.update(state=state_ref, generation=generation_ref)
    head.update(commit=publish(commit, 'commits', '.json'), state_generation=generation_ref)
    path.write_text(json.dumps(head, sort_keys=True, separators=(',', ':')))


def _persist_fixture(root, state, schema, *, closed_v5=False, escaped_contract=False):
    """Arrange flat v4 or a fenced v5 container, without generic set.

    Normal v5 genesis retains v4 payloads. Closed schema-5 payloads are an
    explicit decoder fixture; they have no public migration producer.
    """
    path = root / ".mission-state" / "sessions" / "test.json"
    raw = json.dumps(state).encode()
    if schema == 4:
        path.write_bytes(raw)
        return
    if escaped_contract:
        # Publish valid state, then forge coherent lineage. A hostile JSON
        # escape must reach the reader without using the writer to admit it.
        safe = dict(state)
        safe.pop('acceptance_contract')
        _persist_fixture(root, safe, schema, closed_v5=closed_v5)
        def inject(document):
            document.get('extensions', document)['acceptance_contract'] = state['acceptance_contract']
        _rewrite_fixture_document(root, inject)
        return
    from mission_kernel import decode_snapshot, decode_mission_state, project_legacy_document
    from mission_kernel.codec_v5 import encode_v5_state, _state_payload, _review_json
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.model import MaterializedFindings, SchemaOrigin
    from mission_persistence.fenced_commit import LocalFencedRepository

    snapshot = decode_snapshot(raw)
    path.unlink()
    fixture_time = datetime.now(timezone.utc)
    repository = LocalFencedRepository(root / ".mission-state", clock=lambda: fixture_time)
    request = _request(operation_id="fixture-genesis", lease_id="test-lease",
                       command_type="init", argv=("init", "completion fixture"))
    admitted = repository.begin(request)
    if not closed_v5:
        target = replace(snapshot.state, lease=admitted.pending_lease.target, snapshot_provenance=None)
        repository.initialize(request, state_bytes=project_legacy_document(target))
        assert json.loads(path.read_bytes())["schema"] == "mission-head/1"
        return
    def sized(reference):
        fields = {"size": (root / reference.relative_path).stat().st_size}
        if reference.kind == "review-aggregate":
            fields["iteration"] = state["iteration"]
        return replace(reference, **fields)
    reviews = tuple(sized(reference) for reference in snapshot.state.reviews)
    typed = replace(snapshot.state, schema_origin=SchemaOrigin.V5,
                    legacy_passthrough=None, snapshot_provenance=None,
                    extensions=freeze_json_value(state),
                    reviews=reviews,
                    findings=MaterializedFindings((), reviews),
                    lease=admitted.pending_lease.target)
    # v4 and v5 score payloads have different closed shapes. Decode the
    # canonical v5 fixture before exercising its public persistence route.
    payload = _state_payload(typed, snapshot.guidance)
    payload["scores"] = [{
        "source": score.source.value, "items": entry["items"],
        "composite": entry["composite"], "min_item": entry["min_item"],
        "agreement": entry["review_agreement"], "open_high": entry["open_high"],
        "review_evidence_ref": _review_json(sized(score.source_evidence_ref)),
        "scoring_evidence_ref": {"kind": "scoring-artifact",
                                 "relative_path": score.scoring_evidence_ref.relative_path,
                                 "digest": score.scoring_evidence_ref.digest,
                                 "size": sized(score.scoring_evidence_ref).size},
        "revision_scope": entry["revision_scope"],
    } for score, entry in zip(snapshot.state.scores, state["score_history"])]
    typed = decode_mission_state(json.dumps(payload).encode())
    repository.initialize(request, state_bytes=encode_v5_state(typed, snapshot.guidance))
    assert _persisted_fixture_document(root)["schema_version"] == 5


@pytest.fixture(params=[4, 5], ids=["v4-flat", "v5-container"])
def completion_session(request, state_dir, run_cli):
    root = state_dir.parent
    _commit_candidate(root, {"app.txt": "candidate"})
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                         capture_output=True, text=True).stdout.strip()
    _, ref, claim = write_canonical_review_aggregate(root, [canonical_review({})])
    ref["revision_scope"] = {"kind": "git", "base_sha": sha, "head_sha": sha}
    scoring = root / "score.json"
    scoring.write_text(json.dumps({
        "items": claim["items"], "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"], "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {"score_source": "scoring-json", "review_evidence_ref": ref,
                             "revision_scope": ref["revision_scope"]},
    }))
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=root, check=True)
    policy = _policy()
    policy_path = root / ".mission" / "verifiers.json"
    policy_path.parent.mkdir()
    raw_policy = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    policy_path.write_bytes(raw_policy)
    state = json.loads((state_dir / "sessions" / "test.json").read_bytes())
    state["schema_version"] = 4
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(raw_policy).hexdigest()
    source = root / "contract.json"
    source.write_text(json.dumps(contract))
    run_cli("acceptance-contract", "import", "--input", str(source), cwd=root, check=True)
    state = json.loads((state_dir / "sessions" / "test.json").read_bytes())
    return root, state, request.param


def _reject_unchanged(run_cli, root, args, reason, *, env=None, raw_control=False):
    def document():
        if not raw_control:
            return json.loads(run_cli("get", cwd=root).stdout)
        return _persisted_fixture_document(root)
    before = _public_bytes(root)
    document_before = document()
    public_before = document_before.get("control", document_before)
    result = run_cli(*args, cwd=root, env_extra=env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert reason in result.stderr + result.stdout
    assert _public_bytes(root) == before
    document_after = document()
    public_after = document_after.get("control", document_after)
    for key in ("passes", "loop_active", "phase", "terminal_outcome"):
        assert public_after.get(key) == public_before.get(key)
    return result


@pytest.mark.parametrize("command", ["mark-passes", "closeout"])
def test_pending_contract_rejects_public_completion_atomically(completion_session, run_cli, command):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    result = _reject_unchanged(run_cli, root, [command], "acceptance-coverage-pending")
    if command == "closeout":
        assert json.loads(result.stdout)["ok"] is False


def _append_contract_surrogate(contract, mutation):
    if mutation == "argv-surrogate":
        contract["verifier_policy"]["commands"]["project-test"]["argv"].append("\ud800")
    else:
        contract["criteria"][0]["expected"] += "\ud800"


@pytest.mark.parametrize("mutation,reason,route", [
    ("command-id-list", "acceptance-contract-invalid", "mark-passes"),
    ("output-list-element", "verifier-policy-command-invalid", "mark-passes"),
    ("external-inputs-null", "verifier-policy-command-invalid", "mark-passes"),
    ("argv-missing", "verifier-policy-command-invalid", "mark-passes"),
    *[(mutation, reason, route)
      for mutation, reason in [("argv-surrogate", "canonical-json-invalid"),
                               ("expected-surrogate", "canonical-json-invalid")]
      for route in ["mark-passes", "closeout", "force"]],
])
def test_malformed_frozen_verifier_rejects_public_completion_atomically(completion_session, run_cli, mutation, reason, route):
    root, state, schema = completion_session
    contract = state["acceptance_contract"]
    command = contract["verifier_policy"]["commands"]["project-test"]
    if mutation == "command-id-list":
        contract["criteria"][0]["command_id"] = []
    elif mutation == "output-list-element":
        command["declared_untracked"] = [[]]
    elif mutation == "external-inputs-null":
        command["external_inputs"] = None
    elif mutation == "argv-missing":
        command.pop("argv")
    else:
        _append_contract_surrogate(contract, mutation)
    surrogate = mutation.endswith("surrogate")
    _persist_fixture(root, state, schema, escaped_contract=surrogate)
    args = [route] if route != "force" else ["mark-passes", "--force", "--reason", "fixture", "--approved-by-user"]
    _reject_unchanged(run_cli, root, args, reason, raw_control=surrogate)


@pytest.mark.parametrize("route", ["mark-passes", "closeout", "already-passed", "status", "run", "resume"])
def test_null_contract_is_present_and_rejects_atomically(completion_session, run_cli, route):
    root, state, schema = completion_session
    state["acceptance_contract"] = None
    if route == "already-passed":
        state.update(passes=True, loop_active=False, phase="done", terminal_outcome="completed_pass")
    _persist_fixture(root, state, schema)
    args = {
        "already-passed": ["closeout"],
        "status": ["acceptance-contract", "status"],
        "run": ["verification", "run", "--criterion", "AC1"],
        "resume": ["init", state["mission"], "--force-mission"],
    }.get(route, [route])
    reason = "session-already-initialized" if route == "resume" and schema == 5 else "acceptance-contract-invalid"
    _reject_unchanged(run_cli, root, args, reason)


@pytest.mark.parametrize("mutation,reason", [
    ("command-fields", "verifier-policy-command-invalid"),
    ("contract-fields", "acceptance-contract-invalid"),
    ("command-surrogate", "canonical-json-invalid"),
    ("criterion-surrogate", "canonical-json-invalid"),
    ("digest-surrogate", "canonical-json-invalid"),
])
def test_status_rejects_malformed_persisted_contract_atomically(completion_session, run_cli, mutation, reason):
    root, state, schema = completion_session
    contract = state["acceptance_contract"]
    command = contract["verifier_policy"]["commands"]["project-test"]
    if mutation == "command-fields":
        command.pop("argv")
    elif mutation == "contract-fields":
        contract["criteria"] = []
    elif mutation == "command-surrogate":
        command["argv"].append("\ud800")
    elif mutation == "criterion-surrogate":
        contract["criteria"][0]["command_id"] = "\ud800"
    else:
        contract["criteria"][0]["expected"] = "\ud800"
    surrogate = mutation.endswith("surrogate")
    _persist_fixture(root, state, schema, escaped_contract=surrogate)
    _reject_unchanged(run_cli, root, ["acceptance-contract", "status"], reason,
                      raw_control=surrogate)


@pytest.mark.parametrize("present", [True, False])
def test_status_keeps_valid_and_contractless_sessions_readable(completion_session, run_cli, present):
    root, state, schema = completion_session
    if not present:
        state.pop("acceptance_contract")
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli("acceptance-contract", "status", cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["present"] is present
    assert _public_bytes(root) == before


@pytest.mark.parametrize("blocked", [False, True])
def test_runner_rejects_uncanonical_contract_before_receipt_generation(completion_session, run_cli, blocked):
    root, state, schema = completion_session
    state["acceptance_contract"]["criteria"][0]["expected"] = "\ud800"
    _persist_fixture(root, state, schema, escaped_contract=True)
    args = ["verification", "run", "--criterion", "AC1"]
    if blocked:
        repro = root / "repro.json"
        repro.write_text(json.dumps({"artifact_kind": "counterexample", "content": "proof"}))
        args += ["--repro-input", str(repro)]
    _reject_unchanged(run_cli, root, args, "canonical-json-invalid", raw_control=True)


@pytest.mark.parametrize("mutation", ["argv-missing", "argv-surrogate", "expected-surrogate"])
def test_already_passed_closeout_validates_frozen_commands(completion_session, run_cli, mutation):
    root, state, schema = completion_session
    state.update(passes=True, loop_active=False, phase="done", terminal_outcome="completed_pass")
    if mutation == "argv-missing":
        state["acceptance_contract"]["verifier_policy"]["commands"]["project-test"].pop("argv")
    else:
        _append_contract_surrogate(state["acceptance_contract"], mutation)
    surrogate = mutation.endswith("surrogate")
    _persist_fixture(root, state, schema, escaped_contract=surrogate)
    reason = "canonical-json-invalid" if surrogate else "verifier-policy-command-invalid"
    _reject_unchanged(run_cli, root, ["closeout"], reason, raw_control=surrogate)


def _malformed_verifier_commands(base):
    """One shape table, shared by live-policy and persisted-policy checks."""
    yield None
    for field in base:
        command = copy.deepcopy(base)
        command.pop(field)
        yield command
    for field, value in [
        ("unknown", True), ("id", []), ("argv", "command"), ("argv", [[]]),
        ("argv", ["\x00"]), ("argv", ["\ud800"]),
        ("kind", []), ("timeout_sec", True), ("timeout_sec", "5"), ("timeout_sec", 3601),
        ("output_limit", 0), ("output_limit", 1048577),
        ("relative_cwd", []), ("env", {"KEY": []}), ("env", {"KEY": "\x00"}),
        ("toolchain", {"path": [], "digest": base["toolchain"]["digest"]}),
        ("declared_untracked", None), ("declared_untracked", [{}]),
        ("declared_untracked", ["../outside"]), ("declared_untracked", ["out", "out"]),
        ("external_inputs", None), ("external_inputs", [[]]),
        ("external_inputs", [{"kind": "local-file", "source_path": "input"}]),
        ("external_inputs", [{"kind": "local-file", "source_path": [], "target_path": "input"}]),
        ("external_inputs", [{"kind": "local-file", "source_path": "input", "target_path": "../outside"}]),
        ("external_inputs", [{"kind": "local-file", "source_path": "input", "target_path": "input"}] * 2),
        ("replay", {"command_id": []}), ("test_report", {"format": "junit-xml", "path": "out"}),
    ]:
        yield {**copy.deepcopy(base), field: value}
    replay = {"command_id": "project-test", "max_bytes": 64,
              "allowed_artifact_kinds": ["counterexample"], "relative_path": "repro.json"}
    for field, value in [("command_id", []), ("max_bytes", True),
                         ("command_id", "missing"),
                         ("allowed_artifact_kinds", [[]]), ("relative_path", None)]:
        yield {**copy.deepcopy(base), "replay": {**replay, field: value}}


def test_shared_validator_closes_live_and_frozen_command_fields_before_sets():
    from acceptance_contract import AcceptanceContractError, frozen_verifier_commands
    from mission_application.acceptance import acceptance_contract_status
    from mission_application.artifact import EvidenceFailure
    from mission_application.verifier_policy import VerifierPolicyError, validate

    policy = _policy()
    contract = _contract("fixture-mission")
    contract["verifier_policy"] = {"digest": "sha256:" + "a" * 64, "commands": {}}
    for command in _malformed_verifier_commands(policy["commands"][0]):
        with pytest.raises(VerifierPolicyError):
            validate({**policy, "commands": [command]})
        contract["verifier_policy"]["commands"] = {"project-test": command}
        before = copy.deepcopy(contract)
        with pytest.raises(AcceptanceContractError, match="^verifier-policy-command-invalid$"):
            frozen_verifier_commands(contract)
        with pytest.raises(EvidenceFailure, match="^verifier-policy-command-invalid$"):
            acceptance_contract_status({"acceptance_contract": contract})
        assert contract == before
    # A supported empty output/input list must keep working.
    commands = validate(policy)
    contract["verifier_policy"]["commands"] = commands
    assert frozen_verifier_commands(contract) == commands


def test_status_uses_shared_closed_contract_binding_validation():
    from mission_application.acceptance import acceptance_contract_status
    from mission_application.artifact import EvidenceFailure

    base = _contract("fixture-mission")
    base["verifier_policy"] = {"digest": "sha256:" + "a" * 64,
                               "commands": {"project-test": _policy()["commands"][0]}}
    for field, value in [("criteria", []), ("criteria", {}),
                         ("verifier_policy", {**base["verifier_policy"], "extra": True}),
                         ("verifier_policy", {"commands": base["verifier_policy"]["commands"]}),
                         ("verifier_policy", {"digest": "bad", "commands": base["verifier_policy"]["commands"]}),
                         ("verifier_policy", {"digest": base["verifier_policy"]["digest"], "commands": {}})]:
        with pytest.raises(EvidenceFailure, match="^acceptance-contract-invalid$"):
            acceptance_contract_status({"acceptance_contract": {**base, field: value}})
    for field, value in [("id", []), ("required", "true"), ("command_id", []),
                         ("command_id", "missing")]:
        contract = copy.deepcopy(base)
        contract["criteria"][0][field] = value
        with pytest.raises(EvidenceFailure, match="^acceptance-contract-invalid$"):
            acceptance_contract_status({"acceptance_contract": contract})
    contract = copy.deepcopy(base)
    contract["verifier_policy"]["commands"]["project-test"]["id"] = "other-command"
    with pytest.raises(EvidenceFailure, match="^verifier-policy-command-invalid$"):
        acceptance_contract_status({"acceptance_contract": contract})


@pytest.mark.parametrize("mutation", ["direct-command", "replay-id", "replay-command", "replay-surrogate", "repro-surrogate"])
def test_runner_and_replay_reject_malformed_frozen_commands_atomically(completion_session, run_cli, mutation):
    root, state, schema = completion_session
    policy = _replay_policy()
    raw = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (root / ".mission" / "verifiers.json").write_bytes(raw)
    contract = state["acceptance_contract"]
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    contract["verifier_policy_digest"] = digest
    commands = {command["id"]: command for command in policy["commands"]}
    contract["verifier_policy"] = {"digest": digest, "commands": commands}
    args = ["verification", "run", "--criterion", "AC1"]
    if mutation == "direct-command":
        commands["project-test"]["external_inputs"] = [{}]
    else:
        source = root / "repro.json"
        content = "proof\ud800" if mutation == "repro-surrogate" else "proof"
        source.write_text(json.dumps({"artifact_kind": "counterexample", "content": content}))
        args.extend(["--repro-input", str(source)])
        if mutation == "replay-id":
            commands["project-test"]["replay"]["command_id"] = []
        elif mutation == "replay-command":
            commands["replay-test"]["declared_untracked"] = [[]]
        elif mutation == "replay-surrogate":
            commands["replay-test"]["argv"].append("\ud800")
    surrogate = mutation == "replay-surrogate"
    _persist_fixture(root, state, schema, escaped_contract=surrogate)
    reason = "canonical-json-invalid" if surrogate else "verifier-policy-command-invalid"
    if mutation == "repro-surrogate":
        reason = "replay-input-invalid"
    _reject_unchanged(run_cli, root, args, reason, raw_control=surrogate)


@pytest.mark.parametrize("route", ["get", "next", "init", "new-mission", "freshness", "lane-report"])
def test_authoritative_contract_reads_reject_unencodable_state(completion_session, run_cli, route):
    root, state, schema = completion_session
    state.update(passes=False, loop_active=False, phase="halted", terminal_outcome="failed")
    _append_contract_surrogate(state["acceptance_contract"], "expected-surrogate")
    _persist_fixture(root, state, schema, escaped_contract=True)
    args = [route] if route in {"get", "next", "freshness", "lane-report"} else ["init", state["mission"], "--force-mission"]
    if route == "freshness":
        args.extend(["--state-file", str(root / ".mission-state" / "sessions" / "test.json")])
    if route == "new-mission":
        args.append("--new-mission")
    if route == "init":
        reason = "session-already-initialized" if schema == 5 else "canonical-json-invalid"
    elif route == "new-mission":
        reason = "canonical-json-invalid" if schema == 5 else "--new-mission requires an existing terminal V5"
    else:
        reason = "canonical-json-invalid"
    _reject_unchanged(run_cli, root, args, reason, raw_control=True)


@pytest.mark.parametrize("completion_session", [5], indirect=True, ids=["closed-v5"])
def test_closed_v5_inspection_rejects_unencodable_raw_contract(completion_session, run_cli):
    root, state, schema = completion_session
    _append_contract_surrogate(state["acceptance_contract"], "expected-surrogate")
    _persist_fixture(root, state, schema, closed_v5=True, escaped_contract=True)
    _reject_unchanged(run_cli, root, ["get"], "canonical-json-invalid", raw_control=True)


@pytest.mark.parametrize("route", ["live-import", "frozen-run", "replay-run"])
def test_malformed_url_arguments_reject_before_import_or_execution(completion_session, run_cli, route):
    root, state, schema = completion_session
    policy = _replay_policy() if route == "replay-run" else _policy()
    if route == "live-import":
        policy["commands"][0]["argv"].append("http://[")
    raw = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (root / ".mission" / "verifiers.json").write_bytes(raw)
    contract = state["acceptance_contract"]
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    contract["verifier_policy_digest"] = digest
    commands = {command["id"]: command for command in policy["commands"]}
    contract["verifier_policy"] = {"digest": digest, "commands": commands}
    args = ["verification", "run", "--criterion", "AC1"]
    reason = "verifier-explicit-path-unsupported"
    if route == "live-import":
        args = ["acceptance-contract", "import", "--input", str(root / "contract.json")]
        reason = "verifier-policy-explicit-path-unsupported"
    else:
        identifier = "replay-test" if route == "replay-run" else "project-test"
        commands[identifier]["argv"].append("http://[")
        if route == "replay-run":
            source = root / "repro.json"
            source.write_text(json.dumps({"artifact_kind": "counterexample", "content": "proof"}))
            args.extend(["--repro-input", str(source)])
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, args, reason)


def test_explicit_path_parser_rejects_malformed_command_inputs():
    from mission_application.verifier_policy import explicit_paths_are_supported

    argv = _policy()["commands"][0]["argv"]
    for argument in ["http://[", "%68ttp://[", "--input=http://[",
                     "--override-ini=addopts='http://['", "http://example.invalid\uff0fpath"]:
        assert explicit_paths_are_supported([*argv, argument], {}) is False
    assert explicit_paths_are_supported(argv, {"PYTEST_ADDOPTS": "--input=http://["}) is False


def test_already_passed_contract_closeout_is_not_a_success_shortcut(completion_session, run_cli):
    root, state, schema = completion_session
    state.update(passes=True, loop_active=False, phase="done", terminal_outcome="completed_pass")
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, ["closeout"], "requires completion revalidation")


def test_contractless_completion_and_already_passed_closeout_remain_usable(completion_session, run_cli):
    root, state, schema = completion_session
    state.pop("acceptance_contract")
    _persist_fixture(root, state, schema)
    result = run_cli("closeout", cwd=root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mark_passes"]["passes"] is True
    before = _public_bytes(root)
    again = run_cli("closeout", cwd=root)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["mark_passes"]["already_passed"] is True
    assert _public_bytes(root) == before


@pytest.mark.parametrize("receipt_case,reason", [
    ("matching", "acceptance-fresh-review-pending"),
    ("missing", "acceptance-receipt-missing"),
    ("invalid", "acceptance-receipt-invalid"),
    ("latest-failed", "acceptance-receipt-not-passed"),
    ("latest-blocked", "acceptance-receipt-not-passed"),
    ("stale-contract", "acceptance-receipt-stale"),
    ("stale-definition", "acceptance-receipt-stale"),
    ("stale-policy-binding", "acceptance-receipt-stale"),
    ("stale-candidate", "acceptance-receipt-stale"),
    ("unavailable-candidate", "acceptance candidate capture failed"),
    ("missing-second", "acceptance-receipt-missing"),
    ("stale-policy", "acceptance candidate capture failed"),
])
def test_public_completion_revalidates_latest_receipt(completion_session, run_cli, receipt_case, reason):
    root, state, schema = completion_session
    # Runner receipt is real; only coverage has no producer in this stage.
    state["acceptance_contract"]["coverage"] = {"status": "valid"}
    if receipt_case == "missing-second":
        state["acceptance_contract"]["criteria"].append({**state["acceptance_contract"]["criteria"][0], "id": "AC2"})
    (root / ".mission-state" / "sessions" / "test.json").write_text(json.dumps(state))
    result = run_cli("verification", "run", "--criterion", "AC1", cwd=root)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["status"] == "passed"
    state["verification_receipts"] = [receipt]
    if receipt_case == "missing":
        state.pop("verification_receipts")
    elif receipt_case == "invalid":
        state["verification_receipts"].append({"criterion_id": "AC1", "status": None})
    elif receipt_case.startswith("latest-"):
        state["verification_receipts"].append({**receipt, "status": receipt_case.removeprefix("latest-")})
    elif receipt_case in {"stale-contract", "stale-definition", "stale-policy-binding"}:
        field = {"stale-contract": "contract_digest", "stale-definition": "verifier_definition_digest",
                 "stale-policy-binding": "verifier_policy_digest"}[receipt_case]
        receipt[field] = "sha256:" + "0" * 64
    elif receipt_case == "stale-candidate":
        # Same HEAD, different tracked contents must invalidate the old pass.
        (root / "app.txt").write_text("changed candidate")
    elif receipt_case == "unavailable-candidate":
        (root / "app.txt").unlink()
        (root / "app.txt").symlink_to("missing-input")
    elif receipt_case == "stale-policy":
        with (root / ".mission" / "verifiers.json").open("ab") as stream:
            stream.write(b"\n")
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, ["mark-passes"], reason)


@pytest.mark.parametrize("args,reason", [
    (["set", "passes=true"], "passes"),
    (["set", "terminal_outcome=completed_pass"], "terminal_outcome"),
    (["set", "phase=done"], "phase"),
    (["set", "phase=halted"], "phase"),
    (["set", "schema_version=2"], "schema_version"),
    (["set", "acceptance_contract=null"], "acceptance_contract"),
    (["set", 'acceptance_contract={"coverage":{"status":"valid"}}'], "acceptance_contract"),
    (["set", 'verification_receipts=[{"status":"passed"}]'], "verification_receipts"),
    (["advance", "--phase", "done"], "terminal"),
    (["advance", "--phase", "halted"], "terminal"),
    (["acceptance-contract", "import", "--input", "contract.json"], "acceptance-contract-already-imported"),
])
def test_alternate_completion_and_evidence_writes_reject_atomically(completion_session, run_cli, args, reason):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, args, reason)


@pytest.mark.parametrize("same_mission", [True, False], ids=["resume", "replace"])
def test_reinitialization_cannot_remove_a_frozen_contract(completion_session, run_cli, same_mission):
    from .test_issue2_init_archive import _load_state_module

    root, state, schema = completion_session
    # Use the actual identity producer to exercise both retained v4 branches:
    # same-mission resume and different-mission archival/replacement.
    state["mission_id"] = _load_state_module().mission_id(state["mission"])
    state["acceptance_contract"]["mission_id"] = state["mission_id"]
    _persist_fixture(root, state, schema)
    mission = state["mission"] if same_mission else "replacement mission"
    reason = "acceptance-contract-reinitialization-forbidden" if schema == 4 else "session-already-initialized"
    _reject_unchanged(run_cli, root, ["init", mission, "--force-mission"], reason)


@pytest.mark.parametrize("command", ["halt", "mark-halt"])
def test_halt_stops_a_contract_session_without_claiming_success(completion_session, run_cli, command):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    result = run_cli(command, "--reason", "stop pending work", "--category", "other", cwd=root)
    assert result.returncode == 0, result.stderr
    public = json.loads(run_cli("get", cwd=root).stdout)
    assert public["passes"] is False
    assert public["loop_active"] is False
    assert public["phase"] == "halted"
    assert public["terminal_outcome"] == "failed"
    assert public["acceptance_contract"] == state["acceptance_contract"]


def test_observations_and_caller_boolean_cannot_supply_acceptance_evidence(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    run_cli("set", "acceptance_gate_satisfied=true", cwd=root, check=True)
    result = run_cli("verification", "record", "--iteration", "1", "--stdin", cwd=root,
                     input_text=json.dumps({"schema": "mission-verification/1", "checks": [{
                         "name": "caller-claim", "ok": True, "detail": "acceptance passed",
                     }], "verification_receipts": [{"criterion_id": "AC1", "status": "passed"}],
                         "acceptance_contract": {"coverage": {"status": "valid"}}}))
    assert result.returncode == 0, result.stderr
    public = json.loads(run_cli("get", cwd=root).stdout)
    assert public["verification_history"][-1]["status"] == "passed"
    assert "verification_receipts" not in public
    assert public["acceptance_contract"] == state["acceptance_contract"]
    _reject_unchanged(run_cli, root, ["mark-passes"], "acceptance-coverage-pending")


def test_contract_import_cannot_produce_valid_coverage(completion_session, run_cli):
    root, state, schema = completion_session
    source = root / "contract.json"
    supplied = json.loads(source.read_text())
    supplied["coverage"] = {"status": "valid"}
    source.write_text(json.dumps(supplied))
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, ["acceptance-contract", "import", "--input", str(source)], "coverage-invalid")


def _force_provider(root):
    """Use the existing registered entry-point protocol, not a caller boolean."""
    package = root / "provider"
    package.mkdir()
    source = package / "completion_provider.py"
    source.write_text(
        "import hashlib, json\nfrom datetime import datetime, timezone\n"
        "def verify(request):\n"
        " p = '.mission-state/archive/approval-receipt.json'\n"
        " doc = {key: request[key] for key in ('session_id','mission_id','revision_scope','terminal_object_digest','event_nonce','request_digest')}\n"
        " doc['schema'] = 'mission-force-approval-receipt/1'\n"
        " raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()\n"
        " open(p, 'wb').write(raw)\n"
        " ref = {'kind':'approval-receipt','path':p,'digest':'sha256:'+hashlib.sha256(raw).hexdigest()}\n"
        " return {'schema':'mission-force-approval-response/1','decision':'approved','verifier_id':'fixture-verifier','request_digest':request['request_digest'],'receipt_ref':ref,'verified_at':datetime.now(timezone.utc).isoformat()}\n"
    )
    metadata = package / "completion_provider-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: completion-provider\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text("[mission.approval_verifiers]\nfixture-entry = completion_provider:verify\n")
    config = root / "host-config" / "mission"
    config.mkdir(parents=True)
    (config / "approval-verifiers.json").write_text(json.dumps({
        "schema": "mission-approval-verifier-registry/2", "verifiers": [{
            "id": "fixture-verifier", "entry_point": "fixture-entry",
            "distribution": "completion-provider", "version": "1.0",
            "source_digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
        }],
    }))
    args = ["mark-passes", "--force", "--reason", "bounded override", "--approved-by-user",
            "--approval-evidence-ref", "sha256:" + "a" * 64, "--approved-actor", "role:owner",
            "--approved-at", datetime.now(timezone.utc).isoformat(), "--reason-code", "user-override",
            "--approval-verifier", "fixture-verifier"]
    return args, {"PYTHONPATH": str(package), "XDG_CONFIG_HOME": str(config.parent)}


@pytest.mark.parametrize("contract_enabled", [True, False], ids=["contract", "legacy"])
def test_valid_force_approval_cannot_override_acceptance(completion_session, run_cli, contract_enabled):
    root, state, schema = completion_session
    if not contract_enabled:
        state.pop("acceptance_contract")
    _persist_fixture(root, state, schema)
    args, env = _force_provider(root)
    if contract_enabled:
        _reject_unchanged(run_cli, root, args, "acceptance-coverage-pending", env=env)
    else:
        result = run_cli(*args, cwd=root, env_extra=env)
        assert result.returncode == 0, result.stderr
        assert json.loads(run_cli("get", cwd=root).stdout)["passes"] is True


@pytest.mark.parametrize("stored_case", ["pending", "null", "malformed-command"])
def test_codecs_keep_contract_and_receipt_evidence(completion_session, run_cli, stored_case):
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel import decode_snapshot
    from mission_persistence.fenced_commit import LocalFencedRepository

    root, state, schema = completion_session
    expected = "acceptance-coverage-pending"
    if stored_case == "null":
        state["acceptance_contract"] = None
        expected = "acceptance-contract-invalid"
    elif stored_case == "malformed-command":
        state["acceptance_contract"]["verifier_policy"]["commands"]["project-test"]["external_inputs"] = None
        expected = "verifier-policy-command-invalid"
    state["verification_receipts"] = [{"criterion_id": "AC1", "status": "blocked"}]
    _persist_fixture(root, state, schema, closed_v5=schema == 5)
    raw = ((root / ".mission-state" / "sessions" / "test.json").read_bytes() if schema == 4
           else LocalFencedRepository(root / ".mission-state").read("test").state_bytes)
    snapshot = decode_snapshot(raw)
    if schema == 5:
        raw = encode_v5_state(snapshot.state, snapshot.guidance)
        # Schema 5 keeps evidence in extensions; it cannot be projected into
        # the retained v4 document yet. Prove the lower pure gate still sees it.
        projected = decode_mission_state(raw).extensions.thaw()
    else:
        projected = json.loads(project_legacy_document(decode_mission_state(raw)))
    for field in ("acceptance_contract", "verification_receipts"):
        assert projected[field] == state[field]
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide
    decision = decide(decode_mission_state(raw), MarkPass())
    assert decision.rejection.code == expected
    assert decision.transition is None
    if schema == 5:
        from mission_kernel.json_codec import freeze_json_value
        legacy_extensions = {key: value for key, value in projected.items() if key != "acceptance_contract"}
        legacy_state = replace(decode_mission_state(raw), extensions=freeze_json_value(legacy_extensions))
        legacy_decision = decide(legacy_state, MarkPass(artifact_gate_satisfied=True,
                                                      specialist_gate_satisfied=True, verified_score_index=0))
        assert legacy_decision.accepted, legacy_decision.rejection
        assert legacy_decision.transition.new_state.control.passes is True
    reason = "legacy passthrough is unavailable" if schema == 5 else expected
    _reject_unchanged(run_cli, root, ["mark-passes"], reason)


@pytest.mark.parametrize('args', [
    ('set', 'complexity=Simple'),
    ('advance', '--phase', 'planning'),
    ('activity', 'start', '--kind', 'active', '--reason', 'implementation'),
    ('activity', 'end'),
    ('progress', 'get', '--json'),
    ('progress', 'update', '--total', '10', '--completed', '1', '--json'),
    ('progress', 'clear', '--json'),
    ('mark-halt', '--reason', 'fixture', '--category', 'other'),
    ('refresh-pid', '--no-reactivate'),
], ids=['set', 'advance', 'activity-start', 'activity-end', 'progress-get',
        'progress-update', 'progress-clear', 'mark-halt', 'refresh-pid'])
@pytest.mark.parametrize('completion_session,closed_v5', [(4, False), (5, False), (5, True)],
                         indirect=['completion_session'], ids=['flat-v4', 'container-v4', 'container-v5'])
def test_repository_routes_reject_unencodable_state_without_publication(completion_session, run_cli, args, closed_v5):
    root, state, schema = completion_session
    state['acceptance_contract']['criteria'][0]['expected'] = '\ud800'
    _persist_fixture(root, state, schema, escaped_contract=True, closed_v5=closed_v5)
    result = _reject_unchanged(run_cli, root, args, 'canonical-json-invalid', raw_control=True)
    output = result.stdout + result.stderr
    assert 'state projection cannot be canonically encoded' in output
    assert 'UnicodeEncodeError' not in output
    assert 'surrogates not allowed' not in output


@pytest.mark.parametrize('completion_session,closed_v5', [(4, False), (5, False), (5, True)],
                         indirect=['completion_session'], ids=['flat-v4', 'container-v4', 'container-v5'])
def test_repository_read_port_rejects_unencodable_state(completion_session, closed_v5):
    import contextlib
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository
    from mission_persistence.legacy_v4 import LegacyV4Repository

    root, state, schema = completion_session
    state['acceptance_contract']['criteria'][0]['expected'] = '\ud800'
    _persist_fixture(root, state, schema, escaped_contract=True, closed_v5=closed_v5)
    path = root / '.mission-state' / 'sessions' / 'test.json'
    repository = (LocalFencedRepository(path.parent.parent) if schema == 5 else
                  LegacyV4Repository(lock=contextlib.nullcontext,
                      read_state=lambda: json.loads(path.read_bytes()),
                      write_state=lambda *_args, **_kwargs: pytest.fail('unexpected publication'),
                      backup_state=lambda: pytest.fail('unexpected backup'),
                      add_to_aggregate=lambda: None, remove_from_aggregate=lambda: None))
    before = _public_bytes(root)
    with pytest.raises(FencedCommitError) as caught:
        repository.read('test')
    assert caught.value.code == 'canonical-json-invalid'
    assert str(caught.value) == 'canonical-json-invalid: state projection cannot be canonically encoded'
    assert _public_bytes(root) == before


def test_repository_reads_keep_unicode_and_historical_nonfinite_scores(completion_session, run_cli):
    root, state, schema = completion_session
    state['acceptance_contract']['criteria'][0]['expected'] = '日本語 café'
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli('progress', 'get', '--json', cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_cli('get', cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['acceptance_contract']['criteria'][0]['expected'] == '日本語 café'
    assert _public_bytes(root) == before
    if schema == 4:
        state['score_history'][0]['composite'] = float('nan')
        path = root / '.mission-state' / 'sessions' / 'test.json'
        path.write_text(json.dumps(state, ensure_ascii=False))
        before = _public_bytes(root)
        for args in [('get',), ('progress', 'get', '--json')]:
            result = run_cli(*args, cwd=root)
            assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(run_cli('get', cwd=root).stdout)['acceptance_contract']['criteria'][0]['expected'] == '日本語 café'
        assert _public_bytes(root) == before


@pytest.mark.parametrize('route', ['parallel-closeout', 'parallel-status', 'halt-all',
                                  'permission-preflight', 'codex-preflight', 'codex-preflight-strict', 'list', 'cleanup-stale', 'init-peer',
                                      'stats', 'learning', 'specialists-summary', 'specialists-accounting', 'queue'])
def test_direct_session_routes_reject_unencodable_state(completion_session, run_cli, route):
    root, state, schema = completion_session
    state.update(logical_group_id='group-902', issue_ref='902')
    if route.startswith('parallel'):
        state.update(passes=True, loop_active=False, phase='done', terminal_outcome='completed_pass',
                     lease_expires_at='2000-01-01T00:00:00Z')
        run_cli('parallel-init', '--group-id', 'group-902', '--issue-ref', '902', cwd=root, check=True)
    _persist_fixture(root, state, schema)
    if route.startswith('parallel'):
        _rewrite_fixture_document(root, lambda doc: doc.update(lease_expires_at='2000-01-01T00:00:00Z'))
        # Prove the hostile fixture is otherwise a pass candidate, not an
        # already rejected child that could hide the publication regression.
        healthy = run_cli('parallel-status', '--group-id', 'group-902', cwd=root)
        assert healthy.returncode == 0, healthy.stderr
        assert json.loads(healthy.stdout)['children']['902']['status'] == 'pass'
    _rewrite_fixture_document(root, lambda doc: doc.update(custom_note='\ud800'))
    args = {
        'parallel-closeout': ('parallel-closeout', '--group-id', 'group-902'),
        'parallel-status': ('parallel-status', '--group-id', 'group-902'),
        'halt-all': ('halt', '--all', '--root', str(root), '--reason', 'fixture', '--category', 'other'),
        'permission-preflight': ('permission-preflight', '--json'),
        'codex-preflight': ('codex-preflight', '--json'),
        'codex-preflight-strict': ('codex-preflight', '--json', '--strict'),
        'list': ('list',),
        'cleanup-stale': ('cleanup-stale', '--root', str(root), '--execute'),
        'init-peer': ('init', 'peer fixture', '--issue-ref', '902'),
        'stats': ('stats', '--root', str(root)),
        'learning': ('learning', 'brief', '--root', str(root)),
        'specialists-summary': ('specialists', 'summary', '--json'),
        'specialists-accounting': ('specialists', 'accounting', '--json'),
        'queue': ('queue', 'enqueue', '--from-state',
                  '--issue-ref', '902', '--pr-ref', '903'),
    }[route]
    _reject_unchanged(run_cli, root, args, 'canonical-json-invalid',
                      env={'MISSION_SEARCH_ROOTS': str(root),
                           **({'MISSION_SESSION_ID': 'fresh'} if route == 'init-peer' else {})},
                      raw_control=True)


def test_halt_all_validates_all_sessions_before_publication(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    peer = dict(state, session_id='z-invalid', custom_note='\ud800')
    (root / '.mission-state' / 'sessions' / 'z-invalid.json').write_text(json.dumps(peer))
    _reject_unchanged(run_cli, root,
                      ['halt', '--all', '--root', str(root), '--reason', 'fixture', '--category', 'other'],
                      'canonical-json-invalid', raw_control=True)


def test_audit_rejects_unencodable_sessions_before_snapshot_publication(completion_session, tmp_path_factory):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    _rewrite_fixture_document(root, lambda doc: doc.update(custom_note='\ud800'))
    before = _public_bytes(root)
    target = tmp_path_factory.mktemp('audit-output') / 'snapshot.json'
    script = Path(__file__).resolve().parents[3] / 'scripts' / 'mission-audit.py'
    result = subprocess.run([sys.executable, str(script), '--root', str(root), '--json',
                             '--snapshot-out', str(target)], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'canonical-json-invalid' in result.stdout + result.stderr
    assert not target.exists()
    assert _public_bytes(root) == before


@pytest.mark.parametrize('boundary', ['archive-target', 'frozen-live'])
def test_archive_resolution_rejects_unencodable_state_before_publication(completion_session, run_cli, boundary):
    root, state, schema = completion_session
    state.update(passes=False, loop_active=False, phase='halted', halt_reason='fixture',
                 halt_category='other', terminal_outcome='failed', pid=None, project_root=str(root))
    _persist_fixture(root, state, schema)
    target = root / '.mission-state' / 'archive' / 'state-fixture.json'
    target.parent.mkdir(exist_ok=True)
    archived = dict(state)
    if boundary == 'archive-target':
        archived['custom_note'] = '\ud800'
    target.write_text(json.dumps(archived))
    if boundary == 'frozen-live':
        _rewrite_fixture_document(root, lambda doc: doc.update(custom_note='\ud800'))
    _reject_unchanged(run_cli, root,
                      ['resolve-archive', '--path', str(target), '--status', 'resolved',
                       '--frozen-snapshot', '--json'],
                      'canonical-json-invalid', raw_control=True)


@pytest.mark.parametrize("completion_session", [5], indirect=True, ids=["v5-container"])
def test_queue_from_state_resolves_healthy_v5_scores(completion_session, run_cli):
    root, state, schema = completion_session
    state["custom_note"] = "日本語 café"
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    scope = state["score_history"][-1]["revision_scope"]

    result = run_cli("queue", "enqueue", "--from-state", "--issue-ref", "902",
                     "--pr-ref", "903", cwd=root)

    assert result.returncode == 0, result.stdout + result.stderr
    queued = json.loads(result.stdout)["entry"]
    assert queued["status"] == "queued"
    assert queued["accepted_base_sha"] == scope["base_sha"]
    assert queued["head_sha"] == scope["head_sha"]
    published = json.loads((root / ".mission-state" / "merge-queue.json").read_bytes())
    assert published["entries"][0] == queued
    after = _public_bytes(root)
    assert {key: after[key] for key in before} == before
    assert set(after) - set(before) == {"merge-queue.json"}


@pytest.mark.parametrize("completion_session", [5], indirect=True, ids=["v5-container"])
def test_parallel_closeout_resolves_healthy_v5_pass_child(completion_session, run_cli):
    root, state, schema = completion_session
    state.update(logical_group_id="group-902", issue_ref="902", passes=True,
                 loop_active=False, phase="done", terminal_outcome="completed_pass",
                 custom_note="日本語 café")
    run_cli("parallel-init", "--group-id", "group-902", "--issue-ref", "902",
            cwd=root, check=True)
    _persist_fixture(root, state, schema)
    _rewrite_fixture_document(root, lambda doc: doc.update(lease_expires_at="2000-01-01T00:00:00Z"))
    before = _public_bytes(root)

    result = run_cli("parallel-closeout", "--group-id", "group-902", cwd=root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["outcome"] == "pass"
    manifest_key = "sessions/group-902.group.json"
    manifest = json.loads((root / ".mission-state" / manifest_key).read_bytes())
    assert manifest["status"] == "terminal"
    assert manifest["outcome"] == "pass"
    after = _public_bytes(root)
    assert after[manifest_key] != before[manifest_key]
    assert {key: value for key, value in after.items() if key != manifest_key} == {
        key: value for key, value in before.items() if key != manifest_key
    }
