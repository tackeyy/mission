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
    old = state.fresh_review.requests[0]
    latest, _ = bound_record(old, command.fresh_review_evidence[0].coverage.thaw(),
                             request_id='latest', nonce='latest-nonce')
    if case in ('absent', 'partial'):
        rows = ()
        if case == 'partial':
            latest = FreshReviewRecord(replace(latest.request, criterion_ids=('optional',), candidate_bindings=tuple(replace(b, criterion_id='optional') for b in latest.request.candidate_bindings)),
                latest.prepare_operation_id, latest.prepare_intent_digest, latest.prepare_payload_digest)
            # No whole attempt: the only request covers an optional criterion.
            latest = replace(latest, request=replace(latest.request, criterion_ids=('optional',)))
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
            raw['coverage_receipt']['status'] = 'open'
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
        return FreshReviewCompletionInputs(bindings=bindings)
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
    from .test_issue879_completion_cli import _public_bytes, _persist_fixture
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
    before = _public_bytes(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['effective_coverage'] == 'valid', output['effective_coverage_reason_code']
    assert output['coverage'] == output['imported_coverage'] == {'status': 'pending'}
    assert _public_bytes(tmp_path) == before
    (tmp_path / 'input.txt').write_bytes(b'changed candidate')
    before = _public_bytes(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'open', 'acceptance-fresh-review-stale')
    assert _public_bytes(tmp_path) == before
    (tmp_path / 'input.txt').unlink()
    (tmp_path / 'input.txt').symlink_to(coverage_path)
    before = _public_bytes(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'unavailable', 'acceptance-fresh-review-bindings-unavailable')
    assert _public_bytes(tmp_path) == before
    coverage_path.unlink()
    before = _public_bytes(tmp_path)
    result = status_cli()
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert (output['effective_coverage'], output['effective_coverage_reason_code']) == (
        'unavailable', 'acceptance-fresh-review-evidence-unavailable')
    assert _public_bytes(tmp_path) == before


def test_status_and_completion_execute_the_shared_kernel_coverage_rule(clean, monkeypatch):
    from mission_kernel import fresh_review_coverage as kernel
    from mission_kernel.transitions import acceptance_completion_rejection
    state, command = clean
    calls = []
    original = kernel._judge
    def judge(attempts, criteria, current, inputs, snapshots):
        calls.append((attempts, criteria, current))
        return original(attempts, criteria, current, inputs, snapshots)
    monkeypatch.setattr(kernel, '_judge', judge)
    observed = FreshReviewCompletionInputs(command.fresh_review_evidence, command.fresh_review_bindings)
    output = acceptance_contract_status(document(state), observe_fresh_review=lambda _: observed)
    assert output['effective_coverage'] == 'valid'
    status_calls = list(calls)
    calls.clear()
    assert acceptance_completion_rejection(state, command) is None
    assert any(call in calls for call in status_calls if call[2] == command.fresh_review_bindings)


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
