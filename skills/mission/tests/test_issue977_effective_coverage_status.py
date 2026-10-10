"""Status diagnoses whole coverage without mutating imported evidence.

The table protects display wiring, not a second copy of coverage policy. Kernel
coverage tests retain the detailed priority matrix; completion retains its other
independent gates. Subprocess tests protect service injection and public bytes.
"""
import json
from dataclasses import replace

import pytest

from mission_application.acceptance import acceptance_contract_status
from mission_application.fresh_review_completion import FreshReviewCompletionInputs
from mission_kernel.fresh_review import canonical_bytes, projection_document, FreshReviewProjection
from .test_issue913_completion_judgement import clean, bound_record
from .test_issue913_completion_inputs import published, gate_state
from .test_issue896_completed import completed_carrier


def published_record(record):
    """Give a new attempt its own valid publication/dispatch identities."""
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from mission_kernel.json_codec import freeze_json_value
    key = record.request.request_id
    record = replace(record, prepare_operation_id='prepare-' + key)
    if record.status in ('pending', 'reserved', 'consumed'):
        return replace(record, operation_id=None if record.status == 'pending' else 'dispatch-' + key,
            intent_digest=None if record.status == 'pending' else record.request.contract_digest,
            payload_digest=None if record.status == 'pending' else record.request.contract_digest,
            dispatch=None, launch=None, launch_operation_id=None, independent=None,
            result=freeze_json_value({'legacy': True}) if record.status == 'consumed' else None)
    dispatch = record.dispatch.thaw()
    dispatch.update(invocation_id='inv_' + canonical_digest(key)[7:39], operation_id='dispatch-' + key,
                    reservation_id=reservation_id_for_operation('dispatch-' + key))
    raw = record.result.thaw() if record.result else None
    if raw:
        raw.update(dispatch_operation_id=dispatch['operation_id'], commit_operation_id='commit-' + key)
    launch = raw.get('launch_receipt') if raw else record.launch.thaw()
    if launch:
        launch['operation_id'] = dispatch['operation_id']
        if raw and 'launch_receipt' in raw:
            raw['launch_digest'] = canonical_digest(launch)
    if record.status == 'dispatch-unknown':
        launch = None
    return replace(record, operation_id=dispatch['operation_id'], dispatch=freeze_json_value(dispatch),
        launch=freeze_json_value(launch) if launch else None,
        launch_operation_id=dispatch['operation_id'] if launch else None,
        independent=(launch['context_mode'] == 'fresh') if launch else None,
        result=freeze_json_value(raw) if raw else None)


def document(state):
    return dict(state.legacy_passthrough.thaw(), fresh_review=projection_document(state.fresh_review))


def test_valid_whole_receipt_keeps_imported_coverage_and_state_bytes(clean):
    state, command = clean
    data = document(state)
    before = canonical_bytes(data)
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    result = acceptance_contract_status(data, observe_fresh_review=lambda _: observed)
    assert result['coverage'] == result['imported_coverage'] == {'status': 'pending'}
    assert (result['effective_coverage'], result['effective_coverage_reason_code'],
            result['effective_coverage_request_id']) == ('valid', None, state.fresh_review.requests[0].request.request_id)
    assert canonical_bytes(data) == before


@pytest.mark.parametrize('case,expected,code', [
    ('absent', 'pending', 'acceptance-fresh-review-missing'),
    ('partial', 'pending', 'acceptance-fresh-review-missing'),
    ('withdrawn', 'pending', 'acceptance-fresh-review-missing'),
    ('stale', 'open', 'acceptance-fresh-review-stale'),
    ('pending', 'pending', 'acceptance-fresh-review-pending'),
    ('dispatch-unknown', 'pending', 'acceptance-fresh-review-pending'),
    ('running', 'pending', 'acceptance-fresh-review-pending'),
    ('reserved', 'pending', 'acceptance-fresh-review-pending'),
    ('consumed', 'pending', 'acceptance-fresh-review-pending'),
    ('failed', 'open', 'acceptance-coverage-open'),
    ('blocked', 'open', 'acceptance-coverage-open'),
    ('abandoned-unknown', 'open', 'acceptance-coverage-open'),
    ('non-independent', 'open', 'acceptance-fresh-review-non-independent'),
    ('open', 'open', 'acceptance-coverage-open'),
])
def test_latest_whole_attempt_is_displayed_without_falling_back(clean, case, expected, code):
    from mission_kernel.fresh_review import FreshReviewProjection, FreshReviewRecord, canonical_digest
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue974_withdrawn_completion import tombstone
    from .test_issue909_fresh_review_receipts import terminal_document
    state, command = clean
    if case == 'partial':
        from .test_issue913_completion_judgement import with_contract
        state, command = with_contract(clean, lambda c: c['criteria'].append(dict(c['criteria'][0], id='AC2')))
    old = state.fresh_review.requests[0]
    latest, latest_evidence = bound_record(old, command.fresh_review_evidence[0].coverage.thaw(),
                             request_id='latest', nonce='latest-nonce')
    if case in ('absent', 'partial'):
        rows = ()
        if case == 'partial':
            latest = FreshReviewRecord(latest.request,
                latest.prepare_operation_id, latest.prepare_intent_digest, latest.prepare_payload_digest)
            # The request covers AC1, but the contract now requires AC1 and AC2.
            rows = (latest,)
    elif case == 'withdrawn':
        rows = (old, tombstone(old.request, old.request.criterion_ids))
    else:
        raw = latest.result.thaw()
        if case == 'non-independent':
            raw['independent'] = False
            raw['launch_receipt']['context_mode'] = 'inline'
            raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
        elif case == 'open':
            coverage = latest_evidence.coverage.thaw()
            coverage['status'] = 'open'
            coverage['open_requirement_ids'] = ['R1']
            coverage['requirements'][0].update(status='open', reason_code='unconfirmed')
            latest, latest_evidence = bound_record(latest, coverage)
            raw = latest.result.thaw()
        elif case in ('failed', 'blocked', 'abandoned-unknown'):
            terminal = terminal_document(case)
            for key in ('request_id', 'request_digest', 'candidate_digest', 'nonce'):
                terminal[key] = raw[key]
            if 'launch_receipt' in terminal:
                terminal.update(launch_receipt=raw['launch_receipt'], launch_digest=raw['launch_digest'])
            raw = terminal
        latest = replace(latest, status=case if case not in ('non-independent', 'open', 'stale') else 'completed',
                         result=freeze_json_value(raw) if case not in ('pending', 'dispatch-unknown', 'running', 'reserved') else None,
                         launch=freeze_json_value(raw['launch_receipt']) if 'launch_receipt' in raw else None)
        if case in ('pending', 'reserved', 'consumed'):
            latest = replace(latest, dispatch=None, launch=None, launch_operation_id=None,
                             intent_digest=None, payload_digest=None)
        rows = (old, published_record(latest))
    state = replace(state, fresh_review=FreshReviewProjection(rows))
    bindings = replace(command.fresh_review_bindings,
        input_digests=tuple((r.request.request_id, r.request.input_digest) for r in rows if isinstance(r, FreshReviewRecord)))
    if case == 'stale':
        bindings = replace(bindings, input_digests=(*bindings.input_digests[:-1], ('latest', 'sha256:' + 'b' * 64)))
    def observe(_):
        if case in ('absent', 'partial', 'withdrawn'):
            pytest.fail('missing coverage must not require bindings or old evidence')
        evidence = (*command.fresh_review_evidence, latest_evidence) if rows[-1].status == 'completed' else command.fresh_review_evidence
        return FreshReviewCompletionInputs(evidence, bindings)
    data = document(state)
    before = canonical_bytes(data)
    result = acceptance_contract_status(data, observe_fresh_review=observe)
    request_id = None if case in ('absent', 'partial') else 'withdrawn-request' if case == 'withdrawn' else 'latest'
    assert (result['effective_coverage'], result['effective_coverage_reason_code'], result['effective_coverage_request_id']) == (expected, code, request_id)
    assert canonical_bytes(data) == before


@pytest.mark.parametrize('failure,reason', [
    (OSError('private diagnostic'), 'acceptance-fresh-review-bindings-unavailable'),
    (KeyError('private diagnostic'), 'acceptance-fresh-review-bindings-unavailable'),
    (TypeError('private diagnostic'), 'acceptance-fresh-review-bindings-unavailable'),
    (RecursionError('private diagnostic'), 'acceptance-fresh-review-bindings-unavailable'),
])
def test_unavailable_observation_is_a_read_only_diagnostic(clean, failure, reason):
    state, _ = clean
    data = document(state)
    before = canonical_bytes(data)
    def observe(_):
        raise failure
    result = acceptance_contract_status(data, observe_fresh_review=observe)
    assert (result['effective_coverage'], result['effective_coverage_reason_code'],
            result['effective_coverage_request_id']) == ('unavailable', reason, state.fresh_review.requests[0].request.request_id)
    assert 'private diagnostic' not in json.dumps(result)
    assert canonical_bytes(data) == before


@pytest.mark.parametrize('storage', [4, 5], ids=['flat-v4', 'container-v4'])
def test_public_status_reobserves_bindings_and_preserves_all_published_bytes(tmp_path, clean, monkeypatch, storage):
    from mission_application import fresh_review as prepare
    from mission_application import verification_runner
    from mission_kernel.fresh_review import canonical_digest, candidate_identity
    from .mission_state_fixture_corpus import issue483_corpus
    from .test_issue879_completion_cli import _persist_fixture
    state, command = clean
    old = state.fresh_review.requests[0]
    contract = state.legacy_passthrough.thaw()['acceptance_contract']
    policy = dict(schema='mission-verifier-policy/2', commands=list(contract['verifier_policy']['commands'].values()))
    contract['verifier_policy_digest'] = contract['verifier_policy']['digest'] = canonical_digest(policy)
    from acceptance_contract import canonical_contract_digest
    (tmp_path / 'input.txt').write_bytes(b'candidate')
    monkeypatch.setattr(verification_runner, '_tracked', lambda _: [('input.txt', 0o644)])
    snapshots = prepare._capture(tmp_path, contract['verifier_policy']['commands'])
    # Only tracked-file discovery is synthetic: candidate bytes/digests,
    # packet computation, policy/evidence reads and public CLI are real.
    packet = prepare.build_input_packet(contract, old.request.perspective, snapshots)
    record, _ = bound_record(old, command.fresh_review_evidence[0].coverage.thaw(),
        contract_digest=canonical_contract_digest(contract), verifier_policy_digest=canonical_digest(policy),
        candidate_bindings=tuple(replace(b, snapshot_digest=snapshots[b.command_id].digest) for b in old.request.candidate_bindings),
        input_digest=canonical_digest(packet),
        input_ref=replace(old.request.input_ref, digest=canonical_digest(packet),
                          relative_path='evidence/fresh-review/' + canonical_digest(packet)[7:] + '.json',
                          size=len(canonical_bytes(packet))),
        candidate_digest=candidate_identity({k: v.digest for k, v in snapshots.items()}))
    terminal = record.result.thaw()
    terminal['launch_receipt']['received_input_digest'] = record.request.input_digest
    terminal['launch_digest'] = canonical_digest(terminal['launch_receipt'])
    from mission_kernel.json_codec import freeze_json_value
    record = replace(record, result=freeze_json_value(terminal))
    dispatch = record.dispatch.thaw()
    dispatch['outbound_packet_digest'] = record.request.input_digest
    record = published_record(replace(record, dispatch=freeze_json_value(dispatch)))
    record, evidence = bound_record(record, command.fresh_review_evidence[0].coverage.thaw())
    data = issue483_corpus()['v4']
    data.update(acceptance_contract=contract, fresh_review=projection_document(
        FreshReviewProjection((record,))))
    state_dir = tmp_path / '.mission-state'
    (state_dir / 'sessions').mkdir(parents=True)
    (state_dir / 'sessions/test.json').write_bytes(canonical_bytes(data))
    _persist_fixture(tmp_path, data, storage)
    policy_dir = tmp_path / '.mission'
    policy_dir.mkdir()
    policy = dict(schema='mission-verifier-policy/2', commands=list(contract['verifier_policy']['commands'].values()))
    (policy_dir / 'verifiers.json').write_bytes(canonical_bytes(policy))
    reference = record.result.thaw()['coverage_receipt']['evidence_ref']
    coverage_path = tmp_path / reference['relative_path']
    coverage_path.parent.mkdir(parents=True)
    coverage_path.write_bytes(canonical_bytes(evidence.coverage.thaw()))
    # Use a fresh interpreter and the public argument parser, with synthetic
    # candidate observation so no git operation is needed for this fixture.
    import subprocess
    import sys
    from pathlib import Path
    cli = Path(__file__).parents[1] / 'bin/mission-state.py'
    script = """import sys,runpy
sys.path.insert(0, sys.argv[1])
from mission_application import verification_runner
verification_runner._tracked=lambda root: [('input.txt',420)]
sys.argv=[sys.argv[2],'acceptance-contract','status']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    def status_cli():
        return subprocess.run([sys.executable, '-c', script, str(cli.parent.parent / 'lib'), str(cli)],
                              cwd=tmp_path, capture_output=True, text=True)
    before = state_tree_fingerprint(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['effective_coverage'] == 'valid', output['effective_coverage_reason_code']
    assert output['coverage'] == output['imported_coverage'] == {'status': 'pending'}
    assert state_tree_fingerprint(tmp_path) == before
    (tmp_path / 'input.txt').write_bytes(b'changed candidate')
    before = state_tree_fingerprint(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'open', 'acceptance-fresh-review-stale')
    assert state_tree_fingerprint(tmp_path) == before
    (tmp_path / 'input.txt').unlink()
    (tmp_path / 'input.txt').symlink_to(coverage_path)
    before = state_tree_fingerprint(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'unavailable', 'acceptance-fresh-review-bindings-unavailable')
    assert state_tree_fingerprint(tmp_path) == before
    coverage_path.unlink()
    before = state_tree_fingerprint(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'unavailable', 'acceptance-fresh-review-evidence-unavailable')
    assert state_tree_fingerprint(tmp_path) == before


def test_status_and_completion_execute_the_shared_kernel_coverage_rule(clean, monkeypatch):
    from mission_kernel import fresh_review_coverage as kernel
    from mission_kernel import fresh_review_completion as completion
    from mission_kernel.transitions import acceptance_completion_rejection
    state, command = clean
    calls = []
    original = kernel._judge
    def judge(attempts, criteria, current, inputs, snapshots):
        calls.append((attempts, criteria, current))
        return original(attempts, criteria, current, inputs, snapshots)
    monkeypatch.setattr(kernel, '_judge', judge)
    facts_calls = []
    original_facts = completion._completion_facts
    def facts(*args):
        facts_calls.append(args)
        return original_facts(*args)
    monkeypatch.setattr(completion, '_completion_facts', facts)
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    output = acceptance_contract_status(document(state), observe_fresh_review=lambda _: observed)
    assert output['effective_coverage'] == 'valid'
    status_calls = list(calls)
    status_facts = list(facts_calls)
    calls.clear()
    facts_calls.clear()
    assert acceptance_completion_rejection(state, command) is None
    assert any(call in calls for call in status_calls if call[2] == command.fresh_review_bindings)
    assert status_facts and status_facts == facts_calls


def test_contractless_status_remains_readable_without_observation():
    assert acceptance_contract_status({}) == {'present': False}


def test_public_status_without_an_attempt_keeps_legacy_keys_and_bytes(tmp_path, run_cli):
    from .test_issue877_acceptance_contract import _contract
    from .test_issue879_completion_cli import _public_bytes
    run_cli('init', 'coverage diagnostic', '--force-mission', cwd=tmp_path, check=True)
    state = json.loads(run_cli('get', cwd=tmp_path).stdout)
    contract = _contract(state['mission_id'])
    source = tmp_path / 'contract.json'
    source.write_text(json.dumps(contract))
    run_cli('acceptance-contract', 'import', '--input', str(source), cwd=tmp_path, check=True)
    before = _public_bytes(tmp_path)
    result = run_cli('acceptance-contract', 'status', cwd=tmp_path, check=True)
    output = json.loads(result.stdout)
    assert output['coverage'] == output['imported_coverage'] == contract['coverage']
    assert (output['effective_coverage'], output['effective_coverage_reason_code'], output['effective_coverage_request_id']) == (
        'pending', 'acceptance-fresh-review-missing', None)
    assert _public_bytes(tmp_path) == before


@pytest.mark.parametrize('coverage', [None, 'pending', [], ['pending'], {'status': 'unknown'}])
def test_imported_coverage_preserves_existing_v2_diagnostic_shapes(clean, coverage):
    data = document(clean[0])
    # The v2 status reader predates this feature and accepts canonical JSON
    # coverage shapes beyond the importer's schema. Do not narrow that reader.
    data['acceptance_contract']['coverage'] = coverage
    before = canonical_bytes(data)
    output = acceptance_contract_status(data)
    assert output['coverage'] == output['imported_coverage'] == coverage
    assert output['effective_coverage'] == 'unavailable'
    assert canonical_bytes(data) == before


@pytest.mark.parametrize('fault', ['open-list', 'criterion-result', 'requirement-map',
                                   'finding-requirement', 'replay-binding', 'replay-actual'])
def test_rebound_semantic_faults_share_the_completion_rejection(clean, published, fault):
    from .test_issue913_completion_judgement import command_for
    from mission_kernel.transitions import acceptance_completion_rejection
    state, command = clean
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    findings = []
    if fault == 'open-list':
        coverage['open_requirement_ids'] = ['R1']
    elif fault == 'criterion-result':
        coverage['criterion_results'][0]['criterion_id'] = 'ghost'
    elif fault == 'requirement-map':
        coverage['requirements'][0]['criterion_ids'] = ['ghost']
    else:
        findings = [json.loads(published[4][0])]
        coverage['open_finding_ids'] = ['finding-1']
        if fault == 'finding-requirement':
            findings[0]['requirement_ids'] = ['ghost']
        elif fault == 'replay-binding':
            findings[0]['replay']['candidate_digest'] = 'sha256:' + 'b' * 64
        else:
            findings[0]['actual']['exit_code'] = 0
    record, evidence = bound_record(state.fresh_review.requests[0], coverage, tuple(findings))
    state = replace(state, fresh_review=FreshReviewProjection((record,)))
    command = command_for(state, (evidence,))
    reason = acceptance_completion_rejection(state, command)
    assert reason == 'acceptance-fresh-review-evidence-invalid'
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    result = acceptance_contract_status(document(state), observe_fresh_review=lambda _: observed)
    assert result['effective_coverage'] in ('open', 'unavailable')
    assert result['effective_coverage_reason_code'] == reason


@pytest.mark.parametrize('optional', [False, True], ids=['required-subset', 'optional-excluded'])
def test_status_and_completion_use_the_same_required_criterion_set(clean, optional):
    from .test_issue913_completion_judgement import with_contract, command_for
    from mission_kernel.fresh_review import FreshReviewRecord
    from mission_kernel.transitions import acceptance_completion_rejection
    state, command = with_contract(clean, lambda c: c['criteria'].append(
        dict(c['criteria'][0], id='AC2', required=not optional)))
    record = state.fresh_review.requests[0]
    record = published_record(record) if optional else FreshReviewRecord(record.request,
        record.prepare_operation_id, record.prepare_intent_digest, record.prepare_payload_digest)
    state = replace(state, fresh_review=FreshReviewProjection((record,)))
    command = command_for(state, command.fresh_review_evidence if optional else ())
    # This request names the real AC1 only. AC2 belongs to the contract, but
    # makes this request partial only when AC2 is required.
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    result = acceptance_contract_status(document(state), observe_fresh_review=lambda _: observed)
    reason = None if optional else 'acceptance-fresh-review-missing'
    assert acceptance_completion_rejection(state, command) == reason
    assert result['effective_coverage'] == ('valid' if optional else 'pending')
    assert result['effective_coverage_reason_code'] == reason


@pytest.mark.parametrize('fault,reason', [
    ('missing-carrier', 'acceptance-fresh-review-evidence-invalid'),
    ('digest', 'acceptance-fresh-review-evidence-mismatch'),
    ('bindings', 'acceptance-fresh-review-bindings-invalid'),
])
def test_status_authenticates_carriers_and_bindings_like_completion(clean, fault, reason):
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import acceptance_completion_rejection
    state, command = clean
    if fault == 'missing-carrier':
        command = replace(command, fresh_review_evidence=())
    elif fault == 'digest':
        evidence = command.fresh_review_evidence[0]
        coverage = evidence.coverage.thaw()
        coverage['open_requirement_ids'] = ['R1']
        command = replace(command, fresh_review_evidence=(replace(evidence, coverage=freeze_json_value(coverage)),))
    else:
        command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
                          contract_digest='sha256:' + 'b' * 64))
    assert acceptance_completion_rejection(state, command) == reason
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    result = acceptance_contract_status(document(state), observe_fresh_review=lambda _: observed)
    assert (result['effective_coverage'], result['effective_coverage_reason_code']) == ('unavailable', reason)


def state_tree_fingerprint(root):
    """Include coordination files, directory creation and mtimes, not just public bytes."""
    root = root / '.mission-state'
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None)
            for path in (root, *sorted(root.rglob('*')))}


@pytest.mark.parametrize('storage', [4, 5], ids=['flat-v4', 'container-v4'])
@pytest.mark.parametrize('route', ['acceptance-contract', 'fresh-review'])
def test_status_never_creates_locks_layout_or_recovers_transactions(tmp_path, storage, route, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    from .mission_state_fixture_corpus import issue483_corpus
    from .test_issue879_completion_cli import _persist_fixture
    state_dir = tmp_path / '.mission-state'
    (state_dir / 'sessions').mkdir(parents=True)
    data = issue483_corpus()['v4']
    (state_dir / 'sessions/test.json').write_bytes(canonical_bytes(data))
    _persist_fixture(tmp_path, data, storage)
    lock = state_dir / '.state.lock'
    if lock.exists():
        lock.unlink()
    # Keep the referenced immutable lineage, but remove all empty writer layout.
    for path in sorted(state_dir.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if storage == 5:
        # A writer's begin/recovery would reject this prepare. Diagnostics read
        # only the published head and leave transaction residue untouched.
        pending = state_dir / 'transactions/prepared'
        pending.mkdir(parents=True)
        (pending / 'unfinished.json').write_bytes(b'{unfinished-transaction')
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    cli = Path(__file__).parents[1] / 'bin/mission-state.py'
    before = state_tree_fingerprint(tmp_path)
    result = subprocess.run([sys.executable, str(cli), route, 'status'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert state_tree_fingerprint(tmp_path) == before


def test_fresh_review_status_reads_capacity_from_the_same_snapshot(tmp_path, monkeypatch):
    # 要求一覧と capacity を同じ snapshot から作る。2 回目の読取りは別の状態（B）を返す。
    from types import SimpleNamespace
    from mission_application import fresh_review as module
    state_file = tmp_path / 'state.json'
    state_file.write_text('{}')
    reads = []
    def load_snapshot(path, *args, **kwargs):
        reads.append(path)
        return ('snapshot-A' if len(reads) == 1 else 'snapshot-B'), {}
    def capacity_status(path, *, load_snapshot=load_snapshot):
        return {'from': load_snapshot(path, legacy_compatibility=True)[0]}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, 'decode_projection', lambda data: SimpleNamespace(requests=()))
    def fail(code, status):
        raise AssertionError(code)
    def read_evidence(*_args, **_kwargs):
        raise AssertionError('no evidence is referenced by an empty state')
    services = SimpleNamespace(resolve_state_file=lambda root: state_file, load_snapshot=load_snapshot,
                               capacity_status=capacity_status, fail=fail, read_evidence=read_evidence)
    output = json.loads(module.run_fresh_review_status_cli(SimpleNamespace(), services))
    assert output['capacity'] == {'from': 'snapshot-A'}
    assert len(reads) == 1
