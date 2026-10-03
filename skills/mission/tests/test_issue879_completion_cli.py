"""Completion cannot turn pending acceptance evidence into a public success.

Valid coverage below is a persisted-state fixture with no public producer.
It proves the lower receipt/fresh-review guard is reachable, never a success
path. Actual command receipts still come from the public verification runner.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import subprocess

import pytest

from .conftest import canonical_review, write_canonical_review_aggregate
from .test_issue878_candidate_snapshot import _commit_candidate
from .test_issue878_verification_runner import _contract, _policy
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


def _persist_fixture(root, state, schema, *, closed_v5=False):
    """Arrange flat v4 or a fenced v5 container, without generic set.

    Normal v5 genesis retains v4 payloads. Closed schema-5 payloads are an
    explicit decoder fixture; they have no public migration producer.
    """
    path = root / ".mission-state" / "sessions" / "test.json"
    raw = json.dumps(state).encode()
    if schema == 4:
        path.write_bytes(raw)
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
    assert json.loads(repository.read("test").state_bytes)["schema_version"] == 5


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


def _reject_unchanged(run_cli, root, args, reason, *, env=None):
    before = _public_bytes(root)
    document_before = json.loads(run_cli("get", cwd=root).stdout)
    public_before = document_before.get("control", document_before)
    result = run_cli(*args, cwd=root, env_extra=env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert reason in result.stderr + result.stdout
    assert _public_bytes(root) == before
    document_after = json.loads(run_cli("get", cwd=root).stdout)
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


def test_codecs_keep_contract_and_receipt_evidence(completion_session, run_cli):
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel import decode_snapshot
    from mission_persistence.fenced_commit import LocalFencedRepository

    root, state, schema = completion_session
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
    assert decision.rejection.code == "acceptance-coverage-pending"
    if schema == 5:
        from mission_kernel.json_codec import freeze_json_value
        legacy_extensions = {key: value for key, value in projected.items() if key != "acceptance_contract"}
        legacy_state = replace(decode_mission_state(raw), extensions=freeze_json_value(legacy_extensions))
        legacy_decision = decide(legacy_state, MarkPass(artifact_gate_satisfied=True,
                                                      specialist_gate_satisfied=True, verified_score_index=0))
        assert legacy_decision.accepted, legacy_decision.rejection
        assert legacy_decision.transition.new_state.control.passes is True
    reason = "legacy passthrough is unavailable" if schema == 5 else "acceptance-coverage-pending"
    _reject_unchanged(run_cli, root, ["mark-passes"], reason)
