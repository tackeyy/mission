"""Capacity rejection must leave authoritative state unpublished."""
import copy
import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mission_kernel import state_capacity as sc
from .test_lifecycle_usecases import _load_cli_module
from .test_issue879_completion_cli import completion_session, _persist_fixture, _public_bytes
from .test_issue503_fenced_commit import _commit_cli_init, _request, _public_bytes as _fenced_bytes
from mission_kernel.json_codec import encode_json_value, freeze_json_value


def _canonical(document):
    return encode_json_value(freeze_json_value(document))


@pytest.mark.parametrize('code', ['state-capacity-exhausted', 'state-capacity-legacy-full',
                                  'state-capacity-invariant-broken', 'state-capacity-withdraw-not-needed'])
@pytest.mark.parametrize('writer', ['legacy', 'stage', 'janitor'])
def test_every_writer_propagates_verdict_code_without_publication(tmp_path, monkeypatch, writer, code):
    from mission_persistence import capacity_gate as gate
    from .mission_state_fixture_corpus import issue483_corpus
    cli = _load_cli_module('issue918_wiring')
    if writer == 'stage':
        repository, root, _clock, _sp, source, _result = _commit_cli_init(tmp_path)
        document = json.loads(source)
        admitted = repository.begin(_request(operation_id='next', lease_id=document['lease_id'], argv=('set',)))
        before = _fenced_bytes(root)
        action = lambda: repository._stage_persistence(admitted, state_bytes=_canonical(document), effects=())
    else:
        root = tmp_path
        path = root / '.mission-state/sessions/test.json'
        path.parent.mkdir(parents=True)
        document = issue483_corpus()['v4']
        document.update(mission_id='mission-1', session_id='test', loop_active=True, passes=False, halt_reason='')
        path.write_text(json.dumps(document))
        before = _public_bytes(root)
        action = (lambda: cli.atomic_write_json(path, document, administrative=True, lease_decision=None)) if writer == 'legacy' else (
            lambda: cli._terminalize_state_file(path, root, reason='stop', category='other', set_terminal_phase=True))
    observed = []
    def reject(base, proposed, encoded_len, *, encoding):
        observed.append((base, proposed, encoded_len, encoding))
        verdict = sc.state_capacity_verdict(base, proposed, encoded_len, encoding=encoding)
        return replace(verdict, accepted=False, code=code)
    monkeypatch.setattr(gate, 'state_capacity_verdict', reject)
    with pytest.raises(ValueError, match=code):
        action()
    assert len(observed) == 1
    assert cli._fenced_cli_outcome_kind(code) == 'expected-gate'
    base, proposed, size, encoding = observed[0]
    assert base is not None
    assert encoding is (sc.StateEncoding.CANONICAL if writer == 'stage' else sc.StateEncoding.LEGACY_PRETTY)
    assert size == len(_canonical(proposed) if writer == 'stage' else json.dumps(proposed, indent=2, ensure_ascii=False).encode())
    after = _fenced_bytes(root) if writer == 'stage' else _public_bytes(root)
    if writer == 'janitor':
        # Derived index recovery can precede save, but no rejected backup.
        assert after['sessions/test.json'] == before['sessions/test.json']
        assert 'sessions/test.json.bak' not in after
    else:
        assert after == before


@pytest.mark.parametrize('writer', ['legacy', 'stage', 'janitor'])
def test_raw_padded_halt_reason_cannot_overrun_reserved_slot(tmp_path, writer):
    from .mission_state_fixture_corpus import issue483_corpus
    cli = _load_cli_module('issue918_raw_halt')
    reason = '\x1c' * 2049 + 'stop'
    if writer == 'stage':
        repository, root, _clock, _sp, source, _result = _commit_cli_init(tmp_path)
        document = json.loads(source)
        admitted = repository.begin(_request(operation_id='halt-raw', lease_id=document['lease_id'], argv=('halt',)))
        document.update(halt_reason=reason, loop_active=False, phase='halted')
        before = _fenced_bytes(root)
        action = lambda: repository._stage_persistence(admitted, state_bytes=_canonical(document), effects=())
    else:
        root = tmp_path
        path = root / '.mission-state/sessions/test.json'
        path.parent.mkdir(parents=True)
        document = issue483_corpus()['v4']
        document.update(mission_id='mission-1', session_id='test', loop_active=True, passes=False, halt_reason='')
        path.write_text(json.dumps(document))
        before = _public_bytes(root)
        document['halt_reason'] = reason
        action = (lambda: cli.atomic_write_json(path, document, administrative=True, lease_decision=None)) if writer == 'legacy' else (
            lambda: cli._terminalize_state_file(path, root, reason=reason, category='other', set_terminal_phase=True))
    with pytest.raises(ValueError, match='state-capacity-invariant-broken'):
        action()
    after = _fenced_bytes(root) if writer == 'stage' else _public_bytes(root)
    assert after['sessions/test.json'] == before['sessions/test.json']


@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('field,value,accepted', [
    ('halt_reason', '\x01' * 2048, True), ('halt_reason', '\x1c' * 2049, False),
    ('goal_dispatch_host', '\x01' * 128, True), ('goal_dispatch_host', 'x' * 129, False),
    ('goal_dispatch_resolution_fallback_reason', 'x' * 129, False),
    ('owner_session_id', 'a' * 128, True), ('lease_id', 'a' * 129, False),
    ('lease_id', 'bad token', False), ('fencing_epoch', 0, True),
    ('fencing_epoch', sc.LEASE_EPOCH_MAX, True), ('fencing_epoch', sc.LEASE_EPOCH_MAX + 1, False),
    ('fencing_epoch', True, False), ('fencing_epoch', 1.5, False),
])
def test_shared_writer_bounds_cover_both_layouts(layout, field, value, accepted, historical):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    document, encoding = _base(layout)
    if layout == 'v4':
        target = document
    else:
        target = document['lease' if field in ('owner_session_id', 'lease_id', 'fencing_epoch') else 'extensions']
    target[field] = value
    if layout == 'v5' and field == 'halt_reason':
        document['control']['halt_reason'] = value
    raw = _canonical(document) if layout == 'v5' else json.dumps(document, indent=2, ensure_ascii=False).encode()
    if accepted or historical:
        assert check_state_capacity(raw if historical else None, raw, encoding=encoding).accepted
    else:
        with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
            check_state_capacity(None, raw, encoding=encoding)


@pytest.mark.parametrize('history_count', [sc.STATE_CAPACITY_TAKEOVER_LIMIT,
                                         sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1])
def test_fenced_writer_keeps_reserved_progress_after_halt_and_old_history(tmp_path, history_count):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    from .test_issue933_state_capacity_verdict import _history
    from .test_issue936_state_capacity_reservation import _pending_record
    from mission_kernel.fresh_review import (FreshReviewProjection, projection_document,
                                            reserve_request, consume_request)
    from .test_issue878_verification_runner import _contract
    repository, root, _clock, _sp, source, _result = _commit_cli_init(tmp_path)
    pending = _pending_record()
    def legacy_fixture(document):
        document.update(halt_reason='', loop_active=True, phase='executing',
                        lease_history=_history(history_count), fencing_epoch=history_count + 1,
                        acceptance_contract=_contract(document['mission_id']))
        trial = dict(document, fresh_review=projection_document(FreshReviewProjection((pending,))))
        document['padding'] = 'p' * (sc.STATE_LIMIT - len(_canonical(trial))
            - sc.residual_reservation(trial) - sc.system_remaining(trial) - 128)
    _rewrite_fixture_document(root.parent, legacy_fixture)
    # Preserve the fence binding while seeding a valid pre-E0 history.
    head_path = root / 'sessions/test.json'
    head = json.loads(head_path.read_bytes())
    commit = json.loads((root / head['commit']['path']).read_bytes())
    commit['fencing_epoch'] = history_count + 1
    raw_commit = _canonical(commit)
    digest = hashlib.sha256(raw_commit).hexdigest()
    relative = 'commits/' + digest + '.json'
    (root / relative).write_bytes(raw_commit)
    head['commit'] = {'path': relative, 'digest': 'sha256:' + digest, 'size': len(raw_commit)}
    head_path.write_bytes(_canonical(head))
    lease_id = json.loads(source)['lease_id']
    def publish(projection, operation, *, halt=False):
        admitted = repository.begin(_request(operation_id=operation, lease_id=lease_id, argv=('fixture',)))
        document = json.loads(admitted.base.state_bytes)
        document['fresh_review'] = projection_document(projection)
        if halt:
            document.update(halt_reason='stop', loop_active=False, phase='halted')
        staged = repository._stage_persistence(admitted, state_bytes=_canonical(document), effects=())
        repository.commit(staged, staged.precondition)
        return document
    # The historical state predates the gate; the new item is admitted by it.
    projection = FreshReviewProjection((pending,))
    accepted = publish(projection, 'accept-item')
    assert sc.satisfies_capacity(accepted, len(_canonical(accepted)))
    halted = publish(projection, 'halt-item', halt=True)
    assert sc.state_capacity_verdict(sc.CapacityBase(halted, len(_canonical(halted))),
        halted, len(_canonical(halted)), encoding=sc.StateEncoding.CANONICAL).mode == 'normal'
    reserved = reserve_request(projection, pending.request, operation_id='dispatch-one',
                               intent_digest=pending.prepare_intent_digest, payload_digest=pending.prepare_payload_digest)
    publish(reserved, 'dispatch-item')
    terminal = consume_request(reserved, pending.request, operation_id='dispatch-one',
                               intent_digest=pending.prepare_intent_digest, payload_digest=pending.prepare_payload_digest,
                               result={'status': 'completed', 'output': 'x' * (pending.request.max_output_bytes - 64)})
    final = publish(terminal, 'terminal-item')
    assert final['fresh_review']['requests'][0]['status'] == 'consumed'
    assert len(_canonical(final)) <= sc.STATE_LIMIT


def test_writer_inventory_requires_a_gate_at_every_authoritative_save():
    mission = Path(__file__).resolve().parents[1]
    functions = {}
    for path in [*(mission / 'bin').rglob('*.py'), *(mission / 'lib').rglob('*.py')]:
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef):
                functions[(str(path.relative_to(mission)), node.name)] = ast.unparse(node)
    for path, function, gate in [
        ('bin/mission-state.py', 'atomic_write_json', 'write_legacy_json'),
        ('lib/mission_persistence/legacy_capacity.py', 'write_legacy_json', 'prepare_legacy_json'),
        ('lib/mission_persistence/legacy_capacity.py', 'prepare_legacy_json', 'checked_legacy_state_content'),
        ('bin/mission-state.py', 'write_terminal_state', 'write_legacy_terminal'),
        ('lib/mission_persistence/legacy_capacity.py', 'write_legacy_terminal', 'checked_legacy_state_content'),
        ('lib/mission_persistence/fenced_commit.py', '_stage_persistence', 'check_state_capacity'),
        ('lib/mission_persistence/legacy_capacity.py', 'checked_legacy_state_content', 'check_state_capacity'),
    ]:
        assert gate + '(' in functions[(path, function)]


@pytest.mark.parametrize('halt_present', [False, True])
@pytest.mark.parametrize('command', ['next', 'halt-all', 'cleanup-stale'])
def test_empty_and_absent_halt_slots_keep_over_capacity_stop_behavior(tmp_path, legacy_run_cli, halt_present, command):
    run_cli = legacy_run_cli
    run_cli('init', 'capacity stop fixture', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state.update(pid=99999999, phase='executing', score_history=[],
                 updated_at='2000-01-01T00:00:00Z', last_activity_at='2000-01-01T00:00:00Z')
    for key in ('owner_session_id', 'lease_id', 'fencing_epoch', 'lease_expires_at'):
        state.pop(key, None)
    if halt_present:
        state['halt_reason'] = ''
    else:
        state.pop('halt_reason', None)
    state['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(state)
                             - len(json.dumps(state, indent=2, ensure_ascii=False).encode()) + 4096)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    assert sc.is_over_capacity(state, path.stat().st_size, encoding=sc.StateEncoding.LEGACY_PRETTY)
    args = ('next',) if command == 'next' else ('halt', '--all', '--root', str(tmp_path), '--reason', 'stop') if command == 'halt-all' else (
        'cleanup-stale', '--root', str(tmp_path), '--execute')
    before = path.read_bytes()
    result = run_cli(*args, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    if command == 'next':
        assert path.read_bytes() == before
    else:
        assert json.loads(path.read_bytes())['halt_reason']
        assert json.loads(path.read_bytes())['loop_active'] is False


def test_resume_stale_cannot_release_a_halt_slot_without_capacity(tmp_path, legacy_run_cli):
    run_cli = legacy_run_cli
    run_cli('init', 'capacity resume fixture', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state.update(halt_reason='stale: stopped', halt_category='stale', phase='halted', loop_active=False,
                 terminal_outcome='stale_superseded', resume_target_phase='executing',
                 lease_history=[], score_history=[])
    # No lease is needed for this historical resume; acquisition must itself
    # be proposed under the same capacity decision as the ResumeStale command.
    for key in ('owner_session_id', 'lease_id', 'fencing_epoch', 'lease_expires_at'):
        state.pop(key, None)
    state['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
                             - len(json.dumps(state, indent=2, ensure_ascii=False).encode()) - 4096)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    before = path.read_bytes()
    result = run_cli('resume', '--force', cwd=tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'state-capacity-exhausted' in result.stdout + result.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize('count', [sc.STATE_CAPACITY_TAKEOVER_LIMIT - 1,
                                 sc.STATE_CAPACITY_TAKEOVER_LIMIT,
                                 sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1])
def test_legacy_takeover_writer_enforces_the_history_budget(tmp_path, monkeypatch, count):
    from .test_issue933_state_capacity_verdict import _history
    cli = _load_cli_module('issue918_takeover_budget')
    monkeypatch.setenv('MISSION_SESSION_ID', 'new-owner')
    monkeypatch.setenv('MISSION_LEASE_ID', 'new-lease')
    path = tmp_path / '.mission-state/sessions/new-owner.json'
    path.parent.mkdir(parents=True)
    state = {'schema_version': 4, 'mission_id': 'm', 'loop_active': True,
             'owner_session_id': 'old-owner', 'lease_id': 'old-lease', 'fencing_epoch': count + 1,
             'lease_history': _history(count), 'lease_expires_at': '2000-01-01T00:00:00Z'}
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    if count < sc.STATE_CAPACITY_TAKEOVER_LIMIT:
        cli.atomic_write_json(path, copy.deepcopy(state), administrative=True)
        assert len(json.loads(path.read_bytes())['lease_history']) == count + 1
    else:
        with pytest.raises(ValueError, match='state-capacity-exhausted'):
            cli.atomic_write_json(path, copy.deepcopy(state), administrative=True)
        assert path.read_bytes() == before


def test_legacy_full_status_and_reinit_are_read_only(tmp_path, legacy_run_cli):
    run_cli = legacy_run_cli
    run_cli('init', 'full legacy capacity', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
                             - len(json.dumps(state, indent=2, ensure_ascii=False).encode()) + 1)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    before = path.read_bytes()
    status = run_cli('fresh-review', 'status', cwd=tmp_path)
    capacity = json.loads(status.stdout)['capacity']
    assert capacity['mode'] == 'legacy-full'
    assert capacity['code'] == 'state-capacity-legacy-full'
    assert capacity['headroom'] == 0 and capacity['excess_bytes'] > 0
    assert path.read_bytes() == before
    for args in [('mark-halt', '--reason', 'stop'), ('init', 'replacement')]:
        result = run_cli(*args, cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert 'state-capacity-legacy-full' in result.stdout + result.stderr
        assert path.read_bytes() == before


@pytest.mark.parametrize('writer', ['legacy', 'stage'])
def test_withdrawn_tombstone_shrinks_through_both_writers(tmp_path, writer):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    from .test_issue936_state_capacity_reservation import _pending_record, _minimal_contract
    from mission_kernel.fresh_review import (FreshReviewProjection, projection_document,
                                            withdraw_request, decode_projection, reserve_request)
    pending = _pending_record()
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                 operation_id='withdraw-one', fencing_epoch=1)
    cli = _load_cli_module('issue918_withdraw')
    if writer == 'stage':
        repository, root, _clock, _sp, source, _result = _commit_cli_init(tmp_path)
        def seed(document):
            document.update(fresh_review=projection_document(projection), acceptance_contract=_minimal_contract())
            document['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(document) - len(_canonical(document)) + 100)
        _rewrite_fixture_document(root.parent, seed)
        admitted = repository.begin(_request(operation_id='withdraw', lease_id=json.loads(source)['lease_id'], argv=('fixture',)))
        document = json.loads(admitted.base.state_bytes)
        before_len = len(admitted.base.state_bytes)
        document['fresh_review'] = projection_document(withdrawn)
        staged = repository._stage_persistence(admitted, state_bytes=_canonical(document), effects=())
        repository.commit(staged, staged.precondition)
        stored = json.loads(repository.read('test').state_bytes)
        after_len = len(repository.read('test').state_bytes)
    else:
        path = tmp_path / 'state.json'
        document = {'schema_version': 4, 'mission_id': 'm', 'loop_active': True,
                    'fresh_review': projection_document(projection),
                    'owner_session_id': 'owner', 'lease_id': 'lease', 'fencing_epoch': 1,
                    'lease_expires_at': '9999-12-31T23:59:59Z'}
        document['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(document)
                                   - len(json.dumps(document, indent=2, ensure_ascii=False).encode()) + 100)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False))
        before_len = path.stat().st_size
        document['fresh_review'] = projection_document(withdrawn)
        cli.atomic_write_json(path, document, administrative=True, lease_decision=None)
        stored = json.loads(path.read_bytes())
        after_len = path.stat().st_size
    assert after_len < before_len
    record = decode_projection(stored).requests[0]
    assert record.criterion_ids == pending.request.criterion_ids
    assert record.nonce == pending.request.nonce
    with pytest.raises(ValueError, match='fresh-review-request-withdrawn'):
        reserve_request(decode_projection(stored), pending.request, operation_id='again',
                        intent_digest=pending.prepare_intent_digest, payload_digest=pending.prepare_payload_digest)


def test_fenced_genesis_reserves_system_space_before_any_objects(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository
    _repository, _root, clock, _sp, source, _result = _commit_cli_init(tmp_path)
    root = tmp_path / 'genesis/.mission-state'
    repository = LocalFencedRepository(root, clock=clock)
    document = json.loads(source)
    document['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(document))
    request = _request(operation_id='genesis', lease_id=document['lease_id'], argv=('init',), command_type='init')
    with pytest.raises(ValueError, match='state-capacity-exhausted'):
        repository.initialize(request, state_bytes=_canonical(document))
    assert _fenced_bytes(root) == {}


def test_status_reports_capacity_of_authoritative_bytes(completion_session, run_cli):
    root, state, schema = completion_session
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli('fresh-review', 'status', cwd=root)
    assert result.returncode == 0, result.stderr
    capacity = json.loads(result.stdout)['capacity']
    cli = _load_cli_module('issue918_status')
    snapshot, _ = cli._load_authoritative_state(root / '.mission-state/sessions/test.json')
    assert capacity['encoded_len'] == len(snapshot.state_bytes)
    assert capacity['limit'] == sc.STATE_LIMIT
    assert capacity['headroom'] == (capacity['limit'] - capacity['encoded_len']
                                   - capacity['reserved'] - capacity['system_remaining'])
    assert capacity['mode'] == 'normal'
    assert capacity['code'] is None
    assert _public_bytes(root) == before


def test_legacy_save_reserves_system_space_before_publication(tmp_path):
    cli = _load_cli_module('issue918_legacy')
    path = tmp_path / 'state.json'
    base = {'schema_version': 4, 'mission_id': 'm', 'loop_active': True}
    path.write_text(json.dumps(base))
    proposed = copy.deepcopy(base)
    proposed['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(base))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='state-capacity-exhausted'):
        cli.atomic_write_json(path, proposed, administrative=True, lease_decision=None)
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [
    ('owner', 'bad token'), ('token', 'x' * 129), ('reason', '\x01' * 128),
    ('epoch', -1), ('epoch', sc.LEASE_EPOCH_MAX + 1),
])
def test_lease_admission_rejects_unbounded_values_before_mutating(field, value):
    cli = _load_cli_module('issue918_lease')
    state = {'owner_session_id': 'old', 'lease_id': 'old-token', 'fencing_epoch': 1,
             'lease_expires_at': '2000-01-01T00:00:00Z'}
    if field == 'epoch':
        state['fencing_epoch'] = value
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match='state-capacity-invariant-broken'):
        cli.acquire_or_verify_lease(state, value if field == 'owner' else 'new',
                                   lease_id=value if field == 'token' else 'new-token',
                                   reason=value if field == 'reason' else 'takeover')
    assert state == before


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('epoch', [None, ''])
def test_new_partial_lease_requires_a_bounded_epoch(layout, epoch):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    document, encoding = _base(layout)
    lease = document if layout == 'v4' else document['lease']
    lease.update(owner_session_id='owner', lease_id='token', fencing_epoch=epoch)
    raw = _canonical(document) if layout == 'v5' else json.dumps(document, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(None, raw, encoding=encoding)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
def test_stop_preserves_inherited_legacy_lease_without_bounding_new_tokens(layout):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    lease.update(owner_session_id='old owner', lease_id='old token', fencing_epoch=1,
                 lease_history=[{'owner_session_id': 'historical owner', 'reason': 'old reason'}])
    proposed = copy.deepcopy(base)
    control = proposed if layout == 'v4' else proposed['control']
    control.update(loop_active=False, halt_reason='stop')
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2).encode()
    assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_id'] = 'new invalid token'
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=encoding)


@pytest.mark.parametrize('owner', ['new-owner', 'old owner'])
@pytest.mark.parametrize('epoch', [1, '5', 5.9, True])
def test_legacy_takeover_archives_old_token_with_measured_reservation(tmp_path, epoch, owner):
    cli = _load_cli_module('issue918_migration')
    state = {'schema_version': 4, 'mission_id': 'm', 'loop_active': True,
             'owner_session_id': 'old owner', 'lease_id': 'old token', 'fencing_epoch': epoch,
             'lease_expires_at': '2000-01-01T00:00:00Z', 'lease_history': []}
    path = tmp_path / '.mission-state/sessions/test.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, indent=2))
    lease = cli.acquire_or_verify_lease(state, owner, lease_id='new-token', reason='takeover')
    cli.atomic_write_json(path, state, administrative=True, lease_decision=lease)
    stored = json.loads(path.read_bytes())
    assert stored['lease_history'][0]['owner_session_id'] == 'old owner'
    assert stored['owner_session_id'] == owner


@pytest.mark.parametrize('owner,token,epoch', [
    ('old', 'old-token', sc.LEASE_EPOCH_MAX + 1), ('old owner', 'old token', 1),
])
def test_historical_matching_lease_can_renew_and_stop(tmp_path, owner, token, epoch):
    cli = _load_cli_module('issue918_historical_stop')
    state = {'schema_version': 4, 'mission_id': 'm', 'loop_active': True,
             'owner_session_id': owner, 'lease_id': token, 'fencing_epoch': epoch,
             'lease_expires_at': '2099-01-01T00:00:00Z', 'lease_history': []}
    path = tmp_path / '.mission-state/sessions/test.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, indent=2))
    lease = cli.acquire_or_verify_lease(state, owner, lease_id=token, reason='halt')
    state.update(loop_active=False, halt_reason='stop')
    cli.atomic_write_json(path, state, administrative=True, lease_decision=lease)
    assert json.loads(path.read_bytes())['halt_reason'] == 'stop'


def test_changing_partial_historical_lease_does_not_inherit_missing_epoch():
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    base = {'schema_version': 4, 'mission_id': 'm', 'owner_session_id': 'old',
            'lease_id': 'old-token', 'fencing_epoch': None}
    proposed = dict(base, owner_session_id='new', lease_id='new-token')
    encode = lambda d: json.dumps(d, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)


def test_legacy_token_copy_requires_a_real_takeover():
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    base = {'schema_version': 4, 'mission_id': 'm', 'owner_session_id': 'old owner',
            'lease_id': 'old token', 'fencing_epoch': 1, 'lease_history': []}
    proposed = copy.deepcopy(base)
    proposed['lease_history'].append({'owner_session_id': 'old owner', 'lease_id': 'old token',
                                     'fencing_epoch': 1, 'reason': 'takeover'})
    encode = lambda d: json.dumps(d, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)


@pytest.mark.parametrize('token,expired,accepted', [
    ('', True, True), ('bad token', False, False), ('', False, False), ('x' * 129, False, False),
])
@pytest.mark.parametrize('backup_present', [False, True])
def test_lease_refusal_keeps_guidance_and_backup_bytes(tmp_path, legacy_run_cli, token, expired, accepted, backup_present):
    legacy_run_cli('init', 'lease ordering', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state['lease_expires_at'] = '2000-01-01T00:00:00Z' if expired else '2099-01-01T00:00:00Z'
    path.write_text(json.dumps(state, indent=2))
    backup = path.with_suffix('.json.bak')
    backup.unlink(missing_ok=True)
    if backup_present:
        backup.write_bytes(b'old backup')
    before = path.read_bytes()
    result = legacy_run_cli('refresh-pid', cwd=tmp_path, env_extra={'MISSION_LEASE_ID': token})
    assert result.returncode == (0 if accepted else 2), result.stdout + result.stderr
    if not accepted:
        assert 'lease held' in result.stderr and 'MISSION_LEASE_ID' in result.stderr
        assert path.read_bytes() == before
        assert backup.read_bytes() == b'old backup' if backup_present else not backup.exists()


@pytest.mark.parametrize('terminal,token_ok', [(False, True), (True, True), (True, False)])
def test_init_capacity_preflight_precedes_archives_and_assumptions(tmp_path, legacy_run_cli, terminal, token_ok):
    legacy_run_cli('init', 'old mission', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state.update(loop_active=not terminal, phase='halted' if terminal else 'planning')
    state['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - len(json.dumps(state, indent=2).encode()) + 1)
    path.write_text(json.dumps(state, indent=2))
    before = _public_bytes(tmp_path)
    result = legacy_run_cli('init', 'replacement mission', '--force-mission', cwd=tmp_path,
        env_extra={} if token_ok else {'MISSION_LEASE_ID': 'foreign-token'})
    assert result.returncode == (0 if terminal and token_ok else 2), result.stdout + result.stderr
    if terminal and token_ok:
        assert len(path.read_bytes()) < len(before['sessions/test.json'])
    else:
        assert ('state-capacity-legacy-full' if token_ok else 'lease held') in result.stderr
        assert _public_bytes(tmp_path) == before


@pytest.mark.parametrize('directives', ['a' * 77, 'a' * 200 + '\ngoal_dispatch: b'])
def test_init_bounds_generated_goal_dispatch_fallback(tmp_path, legacy_run_cli, directives):
    result = legacy_run_cli('init', 'goal_dispatch: ' + directives + '\nfix', '--force-mission', cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((tmp_path / '.mission-state/sessions/test.json').read_bytes())
    assert 0 < len(state['goal_dispatch_resolution_fallback_reason']) <= 128


def test_halt_all_continues_after_legacy_full_and_does_not_create_backup(tmp_path, legacy_run_cli):
    legacy_run_cli('init', 'halt inventory', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    full = path.with_name('a-full.json')
    state.update(session_id='a-full')
    state['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - len(json.dumps(state, indent=2).encode()) + 1)
    full.write_text(json.dumps(state, indent=2))
    before = full.read_bytes()
    result = legacy_run_cli('halt', '--all', '--root', str(tmp_path), '--reason', 'stop', cwd=tmp_path)
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload['errors'][0]['error'] == 'state-capacity-legacy-full'
    assert json.loads(path.read_bytes())['halt_reason'] == 'stop'
    assert full.read_bytes() == before and not full.with_suffix('.json.bak').exists()


@pytest.mark.parametrize('history_count', [0, sc.STATE_CAPACITY_TAKEOVER_LIMIT - 1, sc.STATE_CAPACITY_TAKEOVER_LIMIT])
@pytest.mark.parametrize('space', [sc.STATE_CAPACITY_HALT_DELTA + 4096, sc.STATE_CAPACITY_HALT_DELTA - 1])
def test_expired_owner_halt_checks_takeover_and_stop_without_backup_on_refusal(tmp_path, legacy_run_cli, space, history_count):
    from .test_issue933_state_capacity_verdict import _history
    legacy_run_cli('init', 'expired stop', '--force-mission', cwd=tmp_path, check=True)
    path = tmp_path / '.mission-state/sessions/test.json'
    state = json.loads(path.read_bytes())
    state['lease_expires_at'] = '2000-01-01T00:00:00Z'
    state['lease_history'] = _history(history_count)
    state['padding'] = ''
    state['padding'] = 'p' * (sc.STATE_LIMIT - space - len(json.dumps(state, indent=2).encode()))
    path.write_text(json.dumps(state, indent=2))
    before = path.read_bytes()
    result = legacy_run_cli('mark-halt', '--reason', 'stop', cwd=tmp_path, env_extra={'MISSION_LEASE_ID': ''})
    accepted = space > sc.STATE_CAPACITY_HALT_DELTA and history_count < sc.STATE_CAPACITY_TAKEOVER_LIMIT
    assert result.returncode == (0 if accepted else 2), result.stdout + result.stderr
    if accepted:
        stopped = json.loads(path.read_bytes())
        assert stopped['halt_reason'] == 'stop' and len(stopped['lease_history']) == history_count + 1
    else:
        assert path.read_bytes() == before and not path.with_suffix('.json.bak').exists()


@pytest.mark.parametrize('layout', ['v4', 'v5'])
def test_over_capacity_halt_preserves_historical_goal_value(layout):
    from mission_persistence.capacity_gate import check_state_capacity
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    fields = base if layout == 'v4' else base['extensions']
    fields['goal_dispatch_host'] = 'x' * 129
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2, ensure_ascii=False).encode()
    fields['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - 4096 - len(encode(base)) - 40)
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['control']
    target.update(halt_reason='stop', loop_active=False, phase='halted')
    if layout == 'v5':
        proposed['extensions'].update(halt_reason='stop', loop_active=False, phase='halted')
    assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('old_token,epoch,extra_mutation,keep_owner', [('bad token', '5', False, False), ('x' * 129, True, False, False), ('bad token', 1, True, False), ('bad token', 1, False, True)])
def test_excess_takeover_halt_archives_only_the_exact_old_lease(layout, old_token, epoch, extra_mutation, keep_owner):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    lease.update(lease_id=old_token, fencing_epoch=epoch, lease_expires_at='2000-01-01T00:00:00Z')
    if keep_owner:
        lease['owner_session_id'] = 'old owner'
    fields = base if layout == 'v4' else base['extensions']
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2, ensure_ascii=False).encode()
    fields['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - 4096 - len(encode(base)) - 40)
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_history'].append(dict(owner_session_id=lease['owner_session_id'], lease_id=old_token,
        fencing_epoch=int(epoch), reason='halt', at='2026-10-09T00:00:00Z'))
    target.update(owner_session_id=lease['owner_session_id'] if keep_owner else 'new-owner', lease_id='new-token', fencing_epoch=int(epoch) + 1,
        lease_expires_at='2099-01-01T00:00:00Z')
    (proposed if layout == 'v4' else proposed['control']).update(halt_reason='stop', loop_active=False, phase='halted')
    if extra_mutation:
        (proposed if layout == 'v4' else proposed['extensions'])['unrelated'] = True
        with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
            check_state_capacity(encode(base), encode(proposed), encoding=encoding)
    else:
        assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted
