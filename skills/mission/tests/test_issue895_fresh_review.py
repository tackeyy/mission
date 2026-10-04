"""D1: persisted requests cannot be forged, overwritten, or consumed twice."""
from dataclasses import replace
import json

import pytest

from .test_issue879_completion_cli import (
    completion_session, _persist_fixture, _public_bytes, _reject_unchanged,
)

ADAPTER = 'sha256:' + 'a' * 64
ARGS = ('fresh-review', 'prepare', '--perspective', 'counterexamples',
        '--adapter-registration-digest', ADAPTER)


def _prepare(session, run_cli, *, operation='prepare-one'):
    root, state, schema = session
    _persist_fixture(root, state, schema)
    result = run_cli(*ARGS, cwd=root, env_extra={'MISSION_OPERATION_ID': operation})
    assert result.returncode == 0, result.stdout + result.stderr
    return root, json.loads(result.stdout)['request']


def test_public_prepare_publishes_input_and_retries_historical_request(completion_session, run_cli):
    root, request = _prepare(completion_session, run_cli)
    assert request['schema'] == 'mission-fresh-review-request/1'
    assert request['request_id'] != request['nonce']
    assert request['criterion_ids'] == ['AC1']
    assert request['wall_time_sec'] == 300
    assert request['max_tool_calls'] == 64
    assert request['max_replays'] == 16
    assert request['max_output_bytes'] == 256 * 1024
    assert request['max_packet_bytes'] == 1024 * 1024
    packet = (root / request['input_ref']['relative_path']).read_bytes()
    from mission_kernel.fresh_review import canonical_digest
    assert canonical_digest(json.loads(packet)) == request['input_digest']
    assert len(packet) == request['input_ref']['size']
    assert 'score_history' not in json.loads(packet)
    before = _public_bytes(root)
    (root / 'app.txt').write_text('changed after prepare')
    replay = run_cli(*ARGS, cwd=root, env_extra={'MISSION_OPERATION_ID': 'prepare-one'})
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)['request'] == request
    assert _public_bytes(root) == before
    status = run_cli('fresh-review', 'status', cwd=root)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['requests'][0]['status'] == 'pending'
    _reject_unchanged(run_cli, root, [*ARGS[:-2], '--adapter-registration-digest', 'sha256:' + 'b' * 64],
                      'operation', env={'MISSION_OPERATION_ID': 'prepare-one'})
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')


@pytest.mark.parametrize('args,reason', [
    (['set', 'fresh_review=null'], 'dedicated'),
    (['init', 'replace request'], 'fresh-review-reinitialization-forbidden'),
    (['set', 'schema_version=3'], 'schema_version'),
])
def test_generic_writers_cannot_replace_request(completion_session, run_cli, args, reason):
    root, _ = _prepare(completion_session, run_cli)
    if args[0] == 'init' and completion_session[2] == 5:
        reason = 'session-already-initialized'
    _reject_unchanged(run_cli, root, args, reason)


def test_request_decoder_roundtrips_both_codecs_and_rejects_reserved_key(completion_session, run_cli):
    from mission_kernel import decode_mission_state, project_legacy_document, decode_snapshot
    from mission_kernel.codec_v5 import encode_v5_state
    from .mission_state_fixture_corpus import current_v5_open_state
    root, request = _prepare(completion_session, run_cli)
    document = json.loads(run_cli('get', cwd=root).stdout)
    typed = decode_mission_state(json.dumps(document).encode())
    assert typed.fresh_review.requests[0].request.request_id == request['request_id']
    assert json.loads(project_legacy_document(typed))['fresh_review'] == document['fresh_review']
    payload = current_v5_open_state()
    payload['extensions']['fresh_review'] = document['fresh_review']
    closed = decode_mission_state(json.dumps(payload).encode())
    assert closed.fresh_review == typed.fresh_review
    assert decode_mission_state(encode_v5_state(closed, decode_snapshot(json.dumps(payload).encode()).guidance)).fresh_review == typed.fresh_review
    for invalid in (None, {}, {'schema': 'future'}, {'schema': 'mission-fresh-review/1', 'requests': [], 'extra': 1}):
        document['fresh_review'] = invalid
        with pytest.raises(ValueError, match='fresh-review'):
            decode_mission_state(json.dumps(document).encode())
        payload['extensions']['fresh_review'] = invalid
        with pytest.raises(ValueError, match='fresh-review'):
            decode_mission_state(json.dumps(payload).encode())
    with pytest.raises(ValueError, match='fresh-review-projection-mismatch'):
        project_legacy_document(replace(typed, fresh_review=type(typed.fresh_review)()))


def test_budget_and_criterion_rejection_has_no_public_effect(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    for options, reason in [(['--wall-time-sec', '301'], 'fresh-review-budget-invalid'),
                            (['--criterion', 'missing'], 'fresh-review-criterion-invalid')]:
        _reject_unchanged(run_cli, root, [*ARGS, *options], reason)


def test_nonce_reservation_and_consumption_replay_only_exact_operation():
    from mission_kernel.fresh_review import decode_projection, reserve_request, consume_request, canonical_digest, FreshReviewError
    projection = _pure_projection()
    request = projection.requests[0].request
    intent, payload = canonical_digest('dispatch'), canonical_digest({'input': request.input_digest})
    reserved = reserve_request(projection, request, operation_id='dispatch-one', intent_digest=intent, payload_digest=payload)
    assert reserve_request(reserved, request, operation_id='dispatch-one', intent_digest=intent, payload_digest=payload) == reserved
    for operation, i, p, reason in [
        ('dispatch-two', intent, payload, 'fresh-review-nonce-reused'),
        ('dispatch-one', canonical_digest('other'), payload, 'fresh-review-operation-conflict'),
        ('dispatch-one', intent, canonical_digest('other'), 'fresh-review-operation-conflict'),
    ]:
        with pytest.raises(FreshReviewError, match=reason):
            reserve_request(reserved, request, operation_id=operation, intent_digest=i, payload_digest=p)
    result = {'status': 'blocked', 'reason': 'fixture'}
    consumed = consume_request(reserved, request, operation_id='dispatch-one', intent_digest=intent, payload_digest=payload, result=result)
    assert consumed.requests[0].status == 'consumed'
    assert consume_request(consumed, request, operation_id='dispatch-one', intent_digest=intent, payload_digest=payload, result=result) == consumed
    with pytest.raises(FreshReviewError, match='fresh-review-operation-conflict'):
        consume_request(consumed, request, operation_id='dispatch-one', intent_digest=intent, payload_digest=payload, result={'status': 'passed'})
    for field, value in [('candidate_digest', canonical_digest('new')), ('contract_digest', canonical_digest('new')),
                         ('input_digest', canonical_digest('new')), ('iteration', request.iteration + 1)]:
        with pytest.raises(FreshReviewError, match='fresh-review-stale'):
            reserve_request(projection, replace(request, **{field: value}), operation_id='new', intent_digest=intent, payload_digest=payload)


def test_closed_request_rejects_shapes_and_canonical_map():
    from mission_kernel.fresh_review import request_document
    raw = request_document(_pure_projection().requests[0].request)
    from mission_kernel.fresh_review import decode_request, candidate_identity, FreshReviewError
    assert candidate_identity({'z': ADAPTER, 'a': ADAPTER}) == candidate_identity({'a': ADAPTER, 'z': ADAPTER})
    mutations = [('schema', 'future'), ('extra', 1), ('iteration', True), ('max_tool_calls', True),
                 ('nonce', '../escape'), ('created_at', '2026-01-01T00:00:00'), ('criterion_ids', ['AC1', 'AC1']),
                 ('candidate_digest', 'sha256:' + '0' * 64), ('input_ref', None), ('candidate_bindings', [{}])]
    for field, value in mutations:
        with pytest.raises(FreshReviewError):
            decode_request({**raw, field: value})


def _pure_projection():
    from mission_kernel.fresh_review import (FreshReviewProjection, FreshReviewRecord, decode_request,
                                           canonical_digest, candidate_identity)
    input_digest = canonical_digest({'input': 'fixture'})
    request = decode_request({
        'schema': 'mission-fresh-review-request/1', 'request_id': 'request-1', 'nonce': 'nonce-1',
        'mission_id': 'mission-1', 'session_id': 'session-1', 'requirement_digest': ADAPTER,
        'contract_digest': ADAPTER, 'verifier_policy_digest': ADAPTER,
        'candidate_digest': candidate_identity({'command-1': ADAPTER}), 'input_digest': input_digest,
        'adapter_registration_digest': ADAPTER,
        'candidate_bindings': [{'criterion_id': 'AC1', 'role': 'verification', 'command_id': 'command-1',
                                'definition_digest': ADAPTER, 'snapshot_digest': ADAPTER}],
        'criterion_ids': ['AC1'], 'iteration': 1, 'perspective': 'counterexamples', 'allowed_tools': [],
        'wall_time_sec': 300, 'max_tool_calls': 64, 'max_replays': 16, 'max_output_bytes': 262144,
        'max_packet_bytes': 1048576, 'created_at': '2026-01-01T00:00:00+00:00',
        'input_ref': {'kind': 'fresh-review-input', 'relative_path': 'evidence/fresh-review/' + input_digest[7:] + '.json',
                      'digest': input_digest, 'size': 19},
    })
    return FreshReviewProjection((FreshReviewRecord(request, 'prepare-one', ADAPTER, ADAPTER),))


def test_reserved_and_consumed_projection_restore_without_resetting_nonce():
    from mission_kernel.fresh_review import (reserve_request, consume_request, decode_projection, projection_document)
    original = _pure_projection()
    request = original.requests[0].request
    arguments = dict(operation_id='dispatch-one', intent_digest=ADAPTER, payload_digest=ADAPTER)
    reserved = reserve_request(original, request, **arguments)
    consumed = consume_request(reserved, request, result={'status': 'failed'}, **arguments)
    for value in (original, reserved, consumed):
        persisted = json.loads(json.dumps({'fresh_review': projection_document(value)}))
        assert decode_projection(persisted) == value


def test_untrusted_nested_shapes_are_reason_coded():
    import copy
    from mission_kernel.fresh_review import request_document, decode_request, FreshReviewError
    raw = request_document(_pure_projection().requests[0].request)
    invalid = [None, [], {}, True, 0, '', '\ud800']
    # Each field is a distinct trust-boundary check; no subprocess table copies.
    paths = [('candidate_bindings', 0, 'role'), ('candidate_bindings', 0, 'command_id'),
             ('input_ref', 'size'), ('input_ref', 'relative_path'), ('allowed_tools',),
             ('created_at',), ('criterion_ids',), ('max_packet_bytes',)]
    for path in paths:
        for value in invalid:
            payload = copy.deepcopy(raw)
            target = payload
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = value
            # The empty capability list is valid and deliberately grants nothing.
            if path == ('allowed_tools',) and value == []:
                assert decode_request(payload).allowed_tools == ()
            else:
                with pytest.raises(FreshReviewError):
                    decode_request(payload)


def test_packet_budget_and_runtime_commands_remain_closed(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    _reject_unchanged(run_cli, root, [*ARGS, '--max-packet-bytes', '1'], 'fresh-review-packet-too-large')
    _reject_unchanged(run_cli, root, [*ARGS, '--nonce', 'chosen'], 'unrecognized arguments')
    _reject_unchanged(run_cli, root, ['fresh-review', 'run'], 'required')
    result = run_cli('schema', '--contract', 'fresh-review-prepare', cwd=root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['closed'] is True


def test_review_import_preserves_reserved_requests(completion_session, run_cli):
    from .conftest import canonical_review
    root, request = _prepare(completion_session, run_cli)
    review = canonical_review({}, perspective='other-review')
    review['fresh_review'] = {'schema': 'forged', 'requests': []}
    path = root / 'other-review.json'
    path.write_text(json.dumps(review))
    result = run_cli('review-import', '--iteration', '1', '--input', str(path), cwd=root)
    assert result.returncode == 0, result.stderr
    stored = json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)
    assert stored['requests'][0]['request'] == request


def test_new_mission_cannot_discard_pending_requests(completion_session, run_cli):
    completion_session[1]['assumptions_path'] = None
    root, _ = _prepare(completion_session, run_cli)
    run_cli('mark-halt', '--reason', 'test stop', cwd=root, check=True)
    # All init variants refuse replacing the request; V4 already refuses new-mission.
    _reject_unchanged(run_cli, root, ['init', 'replace', '--new-mission', '--force-mission'], 'fresh-review' if completion_session[2] == 5 else 'new-mission')


def test_prepare_binds_all_command_snapshots_and_partial_selection(tmp_path, run_cli):
    from .test_issue878_verification_runner import _prepare_public_runner, _replay_policy
    policy = _replay_policy()
    policy['commands'][1]['external_inputs'] = [{'kind': 'local-file', 'source_path': 'local-input.txt', 'target_path': 'bound.txt'}]
    (tmp_path / 'local-input.txt').write_text('external')
    prepared = _prepare_public_runner(tmp_path, run_cli, policy=policy)
    assert prepared['imported'].returncode == 0, prepared['imported'].stderr
    result = run_cli(*ARGS, '--criterion', 'AC1', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    request = json.loads(result.stdout)['request']
    assert request['iteration'] == 0  # Exact current iteration; prepare is not a score update.
    bindings = request['candidate_bindings']
    assert {item['role'] for item in bindings} == {'verification', 'replay'}
    assert len({item['snapshot_digest'] for item in bindings}) == 2
    from mission_kernel.fresh_review import candidate_identity
    assert request['candidate_digest'] == candidate_identity({item['command_id']: item['snapshot_digest'] for item in bindings})


def test_kernel_prepare_rejects_stale_bindings_and_input(completion_session, run_cli):
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide
    from mission_kernel.commands import PrepareFreshReview, FreshReviewInputEffectClaim
    from mission_kernel.fresh_review import decode_request, canonical_digest
    from mission_kernel.json_codec import freeze_json_value
    root, raw = _prepare(completion_session, run_cli)
    state = decode_mission_state(json.dumps(completion_session[1]).encode())
    packet = json.loads((root / raw['input_ref']['relative_path']).read_bytes())
    request = decode_request(raw)
    ref = request.input_ref
    command = PrepareFreshReview(request, 'prepare-new', ADAPTER, ADAPTER, freeze_json_value(packet),
                                 FreshReviewInputEffectClaim(ref.kind, ref.relative_path, ref.digest, ref.size))
    assert decide(state, command).accepted
    for field, value in [('contract_digest', ADAPTER), ('candidate_digest', ADAPTER), ('input_digest', ADAPTER),
                         ('iteration', request.iteration + 1), ('verifier_policy_digest', ADAPTER)]:
        decision = decide(state, replace(command, request=replace(request, **{field: value})))
        assert not decision.accepted
        assert decision.rejection.code.startswith('fresh-review-')
    decision = decide(state, replace(command, packet=freeze_json_value({**packet, 'perspective': 'changed'})))
    assert not decision.accepted and decision.rejection.code == 'fresh-review-stale'


def test_kernel_rejects_malformed_typed_prepare_without_internal_error():
    from .mission_state_fixture_corpus import current_v5_open_state
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import PrepareFreshReview
    from mission_kernel.transitions import decide
    state = decode_mission_state(json.dumps(current_v5_open_state()).encode())
    command = PrepareFreshReview({}, 'operation', ADAPTER, ADAPTER, None, None)
    decision = decide(state, command)
    assert not decision.accepted
    assert decision.rejection.code == 'fresh-review-request-shape-invalid'


def _tamper_persisted_fresh_review(root, malformed, *, force_legacy_fallback=False):
    from .test_issue879_completion_cli import _rewrite_fixture_document

    def mutate(document):
        document.get('extensions', document)['fresh_review'] = malformed
        if force_legacy_fallback and document.get('schema_version') != 5:
            document['threshold'] = 'historical value'
    _rewrite_fixture_document(root, mutate)


@pytest.mark.parametrize('malformed,reason,force_legacy_fallback', [
    ({'schema': 'future', 'requests': []}, 'fresh-review-projection-schema-invalid', False),
    (None, 'fresh-review-projection-shape-invalid', True),
])
def test_authoritative_reads_and_writes_reject_forged_projection(
    completion_session, run_cli, malformed, reason, force_legacy_fallback,
):
    root, _ = _prepare(completion_session, run_cli)
    if completion_session[2] == 5:
        prepared_state = json.loads(run_cli('get', cwd=root).stdout)
        _persist_fixture(root, prepared_state, 5, closed_v5=True, operation_id='closed-extension-fixture')
    _tamper_persisted_fresh_review(root, malformed, force_legacy_fallback=force_legacy_fallback)
    before = _public_bytes(root)
    for args in [('get',), ('get', '--field', 'fresh_review'), ('set', 'complexity=Simple')]:
        result = run_cli(*args, cwd=root, env_extra={'MISSION_OPERATION_ID': 'reject-forged-state'})
        assert result.returncode == 2, result.stdout + result.stderr
        assert reason in result.stdout + result.stderr
        assert _public_bytes(root) == before


def test_authoritative_fallback_consumers_do_not_skip_forged_requests(completion_session, run_cli):
    root, _ = _prepare(completion_session, run_cli)
    _tamper_persisted_fresh_review(root, {'schema': 'mission-fresh-review/1', 'requests': [], 'extra': 1})
    before = _public_bytes(root)
    for args in [('next',), ('list',), ('stats', '--json'), ('codex-preflight',),
                 ('permission-preflight',), ('progress', 'get', '--json'),
                 ('repair-aggregate-index',), ('repair-aggregate-index', '--execute'),
                 ('init', 'replacement', '--new-mission')]:
        result = run_cli(*args, cwd=root, env_extra={'MISSION_OPERATION_ID': 'reject-forged-fallback'})
        assert result.returncode == 2, (args, result.stdout, result.stderr)
        assert 'fresh-review-projection-shape-invalid' in result.stdout + result.stderr, args
        assert _public_bytes(root) == before
    import subprocess
    import sys
    from pathlib import Path
    audit = Path(__file__).resolve().parents[3] / 'scripts' / 'mission-audit.py'
    result = subprocess.run([sys.executable, str(audit), '--root', str(root), '--json'],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'fresh-review-projection-shape-invalid' in result.stdout + result.stderr
    assert _public_bytes(root) == before


def test_legacy_tolerance_and_absent_fresh_review_are_preserved(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli('get', cwd=root)
    assert result.returncode == 0, result.stderr
    assert 'fresh_review' not in json.loads(result.stdout)
    assert _public_bytes(root) == before
    if schema == 4:
        path = root / '.mission-state' / 'sessions' / 'test.json'
        state.update(threshold='historical value', acceptance_contract=None)
        path.write_text(json.dumps(state))
        before = _public_bytes(root)
        result = run_cli('get', cwd=root)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['acceptance_contract'] is None
        assert _public_bytes(root) == before


def test_fresh_review_status_rejects_unencodable_state_without_publication(completion_session, run_cli):
    root, state, schema = completion_session
    state['acceptance_contract']['criteria'][0]['expected'] = '\ud800'
    _persist_fixture(root, state, schema, escaped_contract=True)
    result = _reject_unchanged(run_cli, root, ('fresh-review', 'status'),
                               'canonical-json-invalid', raw_control=True)
    output = result.stdout + result.stderr
    assert 'state projection cannot be canonically encoded' in output
    assert 'UnicodeEncodeError' not in output
    assert 'surrogates not allowed' not in output


def test_repository_status_keeps_unicode_and_historical_nonfinite_scores(completion_session, run_cli):
    root, state, schema = completion_session
    state['acceptance_contract']['criteria'][0]['expected'] = '日本語 café'
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli('fresh-review', 'status', cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'requests': []}
    assert _public_bytes(root) == before
    if schema == 4:
        # Historical get uses its compatibility reader. Repository selection
        # already rejects non-finite scores for status; retain that distinction.
        state['score_history'][0]['composite'] = float('nan')
        path = root / '.mission-state' / 'sessions' / 'test.json'
        path.write_text(json.dumps(state, ensure_ascii=False))
        before = _public_bytes(root)
        result = run_cli('get', cwd=root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)['acceptance_contract']['criteria'][0]['expected'] == '日本語 café'
        assert _public_bytes(root) == before
