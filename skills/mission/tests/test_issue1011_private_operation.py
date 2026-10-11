"""Private operation fixtures, without credentials or a native host."""
import hashlib
import json
import os
import stat

import pytest

from fresh_review_observation import ObservationBlocked
from fresh_review_store import PrivateOperationStore


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def evidence():
    return dict(session_id='parent-query', boot_id='boot', operation_id='operation',
                request_digest=digest(b'request'), context_digest=digest(b'context'),
                custody='owned')


def test_stable_copy_survives_callback_input_removal(tmp_path):
    source = tmp_path / 'callback'
    source.mkdir()
    raw = b'Neutral immutable request.'
    (source / 'request.txt').write_bytes(raw)
    store = PrivateOperationStore(tmp_path)
    stored = store.copy_input(source, 'request.txt', digest(raw))
    (source / 'request.txt').unlink()
    source.rmdir()
    assert store.read(stored) == raw
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.path / stored.name).stat().st_mode) == 0o600


def test_copy_limit_is_cumulative_across_request_and_snapshots(tmp_path):
    source = tmp_path / 'callback'
    source.mkdir()
    raw = b'x' * (5 * 1024 * 1024)
    (source / 'snapshot').write_bytes(raw)
    store = PrivateOperationStore(tmp_path)
    store.copy_input(source, 'snapshot', digest(raw))
    with pytest.raises(ObservationBlocked):
        store.copy_input(source, 'snapshot', digest(raw))
    assert len(list(store.path.glob('input-*'))) == 1


def test_identical_replacement_is_not_the_owned_copy(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    path = store.path / stored.name
    path.rename(store.path / 'displaced')
    path.write_bytes(b'request')
    path.chmod(0o600)
    with pytest.raises(ObservationBlocked):
        store.read(stored)


def test_evidence_is_selected_projection_and_cannot_store_capability(tmp_path):
    store = PrivateOperationStore(tmp_path)
    record = evidence()
    stored = store.record_evidence(record)
    assert json.loads(store.read(stored)) == record
    with pytest.raises(ObservationBlocked):
        store.record_evidence(dict(record, capability='not-file-material'))
    assert len(list(store.path.glob('evidence-*'))) == 1


def test_write_failure_does_not_delete_replacement_inode(tmp_path, monkeypatch):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path)
    target = store.path / 'input-0'
    def exchange_and_fail(fd, raw):
        target.rename(store.path / 'displaced')
        target.write_bytes(b'other operation material')
        target.chmod(0o600)
        raise OSError('synthetic write failure')
    monkeypatch.setattr(os, 'write', exchange_and_fail)
    with pytest.raises(ObservationBlocked):
        store.copy_input(tmp_path, 'request', digest(b'request'))
    assert target.read_bytes() == b'other operation material'


def test_ttl_boundary_removes_only_owned_operation(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 1000)
    store.copy_input(tmp_path, 'request', digest(b'request'))
    assert store.cleanup(now=1599) is False
    assert store.path.exists()
    assert store.cleanup(now=1600) is True
    assert not store.path.exists()
    assert (tmp_path / 'request').read_bytes() == b'request'


def test_directory_replacement_cannot_redirect_copy_or_cleanup(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    store.path.rename(tmp_path / 'original-operation')
    store.path.mkdir(mode=0o700)
    assert store.cleanup(now=600) is False
    assert store.path.exists()
    with pytest.raises(ObservationBlocked):
        store.copy_input(tmp_path, 'request', digest(b'request'))
    assert not list(store.path.iterdir())


def test_cleanup_continues_past_unknown_and_can_retry_missing_owned_files(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    first = store.copy_input(tmp_path, 'request', digest(b'request'))
    second = store.copy_input(tmp_path, 'request', digest(b'request'))
    changed = store.path / first.name
    changed.rename(tmp_path / 'displaced')
    changed.write_bytes(b'foreign file')
    changed.chmod(0o600)
    assert store.cleanup(now=600) is False
    assert not (store.path / second.name).exists()
    assert changed.read_bytes() == b'foreign file'
    changed.unlink()  # Only the fixture owns the replacement.
    assert store.cleanup(now=601) is True


def test_next_startup_recovers_expired_operation_with_recorded_file_identity(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    old = PrivateOperationStore(tmp_path, now=lambda: 0)
    old.copy_input(tmp_path, 'request', digest(b'request'))
    current = PrivateOperationStore(tmp_path, now=lambda: 100)
    current.copy_input(tmp_path, 'request', digest(b'request'))
    assert PrivateOperationStore.recover_expired(tmp_path, now=599) == ()
    assert PrivateOperationStore.recover_expired(tmp_path, now=600) == (old.path.name,)
    assert not old.path.exists()
    assert current.path.exists()


def test_unknown_file_preserves_manifest_for_later_startup_retry(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    unknown = store.path / 'foreign'
    unknown.write_bytes(b'not owned')
    assert store.cleanup(now=600) is False
    assert not (store.path / stored.name).exists()
    unknown.unlink()
    assert PrivateOperationStore.recover_expired(tmp_path, now=601) == (store.path.name,)


@pytest.mark.parametrize('fault', ['duplicate-field', 'deep-json', 'invalid-utf8'])
def test_unreadable_manifest_cannot_authorize_startup_deletion(tmp_path, fault):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    manifest = max(store.path.glob('manifest-*.json'), key=lambda path: int(path.stem.split('-')[1]))
    raw = manifest.read_bytes()
    if fault == 'duplicate-field':
        raw = raw[:-1] + b',"created":0}'
    elif fault == 'deep-json':
        raw = b'[' * 12000 + b'0' + b']' * 12000
    else:
        raw = b'\xff'
    manifest.write_bytes(raw)
    assert PrivateOperationStore.recover_expired(tmp_path, now=600) == ()
    assert (store.path / stored.name).read_bytes() == b'request'


def test_directory_exchange_during_cleanup_does_not_remove_replacement(tmp_path, monkeypatch):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    rename = os.rename
    def exchange_after_capture(name, destination, **kwargs):
        rename(name, destination, **kwargs)
        if name == stored.name:
            store.path.rename(tmp_path / 'displaced-operation')
            store.path.mkdir(mode=0o700)
    monkeypatch.setattr(os, 'rename', exchange_after_capture)
    assert store.cleanup(now=600) is False
    assert store.path.is_dir()


def test_directory_exchange_during_private_read_refuses_result(tmp_path, monkeypatch):
    import fresh_review_store
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    reader = fresh_review_store.read_job
    def exchange_after_read(*args, **kwargs):
        raw = reader(*args, **kwargs)
        store.path.rename(tmp_path / 'displaced-operation')
        store.path.mkdir(mode=0o700)
        return raw
    monkeypatch.setattr(fresh_review_store, 'read_job', exchange_after_read)
    with pytest.raises(ObservationBlocked):
        store.read(stored)


@pytest.mark.parametrize('invalid', [True, float('nan'), float('inf'), -1, 10 ** 1000, 1e30],
                         ids=['bool', 'nan', 'inf', 'negative', 'huge-int', 'imprecise-float'])
def test_invalid_clock_cannot_create_an_unbounded_or_immediate_expiry(tmp_path, invalid):
    with pytest.raises(ObservationBlocked):
        PrivateOperationStore(tmp_path, now=lambda: invalid)
    assert not list(tmp_path.iterdir())


def test_evidence_retains_native_identity_vocabulary_from_query_identity(tmp_path):
    from fresh_review_observation import QueryIdentity
    owner = object()
    identity = QueryIdentity(owner)
    identity.session_start(owner, {'source': 'startup', 'session_id': 'native:query.1'})
    identity.init(owner, {'session_id': 'native:query.1'})
    record = evidence()
    record['session_id'] = identity.observe()
    store = PrivateOperationStore(tmp_path)
    stored = store.record_evidence(record)
    assert json.loads(store.read(stored))['session_id'] == 'native:query.1'


def test_failed_initial_metadata_keeps_unknown_private_files_without_growth(tmp_path, monkeypatch):
    def fail_sync(fd):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(os, 'fsync', fail_sync)
    with pytest.raises(ObservationBlocked):
        PrivateOperationStore(tmp_path)
    paths = tuple(sorted(tmp_path.rglob('*')))
    assert len(list(tmp_path.iterdir())) == 1
    assert PrivateOperationStore.recover_expired(tmp_path, now=2 ** 40) == ()
    assert tuple(sorted(tmp_path.rglob('*'))) == paths


@pytest.mark.parametrize('size', [8 * 1024 * 1024 - 1, 8 * 1024 * 1024, 8 * 1024 * 1024 + 1],
                         ids=['below', 'exact', 'above'])
def test_copy_byte_boundary_is_inclusive_and_refusal_leaves_no_copy(tmp_path, size):
    raw = b'x' * size
    (tmp_path / 'input').write_bytes(raw)
    store = PrivateOperationStore(tmp_path)
    if size > 8 * 1024 * 1024:
        with pytest.raises(ObservationBlocked):
            store.copy_input(tmp_path, 'input', digest(raw))
        assert not list(store.path.glob('input-*'))
    else:
        stored = store.copy_input(tmp_path, 'input', digest(raw))
        assert len(store.read(stored)) == size


def test_evidence_quota_accumulates_and_failed_record_does_not_consume_capacity(tmp_path, monkeypatch):
    import fresh_review_store
    store = PrivateOperationStore(tmp_path)
    first = store.record_evidence(evidence())
    size = len(store.read(first))
    monkeypatch.setattr(fresh_review_store, 'EVIDENCE_PAYLOAD_LIMIT', size * 2)
    with pytest.raises(ObservationBlocked):
        store.record_evidence(dict(evidence(), credential='not-file-material'))
    second = store.record_evidence(evidence())
    assert len(store.read(second)) == size
    with pytest.raises(ObservationBlocked):
        store.record_evidence(evidence())
    assert len(list(store.path.glob('evidence-*'))) == 2


@pytest.mark.parametrize('invalid', ['false', 1, None])
def test_cleanup_flag_must_be_boolean_before_any_deletion(tmp_path, invalid):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    with pytest.raises(ObservationBlocked):
        store.cleanup(now=100, force=invalid)
    assert store.read(stored) == b'request'


def test_exchange_after_cleanup_stat_never_deletes_another_inode(tmp_path, monkeypatch):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    target = store.path / stored.name
    displaced = tmp_path / 'original-copy'
    unlink, rename = os.unlink, os.rename
    exchanged = False
    def exchange(name):
        nonlocal exchanged
        if not exchanged and name == stored.name:
            exchanged = True
            rename(target, displaced)
            target.write_bytes(b'concurrent same-user replacement')
            target.chmod(0o600)
    def synchronized_unlink(name, *args, **kwargs):
        exchange(name)
        return unlink(name, *args, **kwargs)
    def synchronized_rename(source, destination, *args, **kwargs):
        exchange(source)
        return rename(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, 'unlink', synchronized_unlink)
    monkeypatch.setattr(os, 'rename', synchronized_rename)
    assert store.cleanup(now=600) is False
    assert exchanged
    retained = ([target] if target.exists() else list(store.path.glob('quarantine-*/slot')))
    assert any(path.read_bytes() == b'concurrent same-user replacement' for path in retained)
    assert displaced.read_bytes() == b'request'


@pytest.mark.parametrize('phase', ['before-capture', 'after-capture', 'after-slot-delete', 'after-journal-delete'])
def test_interrupted_cleanup_journal_retries_without_allocating_again(tmp_path, monkeypatch, phase):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    stored = store.copy_input(tmp_path, 'request', digest(b'request'))
    rename, unlink = os.rename, os.unlink
    interrupted = False
    def capture(source, destination, **kwargs):
        nonlocal interrupted
        if not interrupted and source == stored.name:
            if phase == 'before-capture':
                interrupted = True
                raise OSError('synthetic interruption before capture')
            rename(source, destination, **kwargs)
            if phase == 'after-capture':
                interrupted = True
                raise OSError('synthetic interruption after capture')
            return
        return rename(source, destination, **kwargs)
    def erase(name, **kwargs):
        nonlocal interrupted
        unlink(name, **kwargs)
        if not interrupted and ((phase == 'after-slot-delete' and name == 'slot')
                                or (phase == 'after-journal-delete' and name == 'journal.json')):
            interrupted = True
            raise OSError('synthetic interruption after private deletion')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'rename', capture)
        patch.setattr(os, 'unlink', erase)
        assert store.cleanup(now=600) is False
    assert interrupted
    if phase == 'after-journal-delete':
        retained = tuple(sorted(store.path.rglob('*')))
        assert PrivateOperationStore.recover_expired(tmp_path, now=601) == ()
        assert PrivateOperationStore.recover_expired(tmp_path, now=602) == ()
        assert tuple(sorted(store.path.rglob('*'))) == retained
    else:
        assert PrivateOperationStore.recover_expired(tmp_path, now=601) == (store.path.name,)
        assert not store.path.exists()


def test_immutable_manifest_collision_cannot_overwrite_an_unknown_generation(tmp_path):
    (tmp_path / 'request').write_bytes(b'request')
    store = PrivateOperationStore(tmp_path)
    foreign = store.path / 'manifest-1.json'
    foreign.write_bytes(b'concurrent same-user material')
    foreign.chmod(0o600)
    with pytest.raises(ObservationBlocked):
        store.copy_input(tmp_path, 'request', digest(b'request'))
    assert foreign.read_bytes() == b'concurrent same-user material'


def test_empty_operation_directory_exchange_is_preserved_in_quarantine(tmp_path, monkeypatch):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    rename = os.rename
    displaced = tmp_path / 'original-empty-operation'
    replacement_identity = None
    def exchange(source, destination, **kwargs):
        nonlocal replacement_identity
        if source == store.path.name and replacement_identity is None:
            rename(store.path, displaced)
            store.path.mkdir(mode=0o700)
            replacement_identity = store.path.stat().st_ino
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(os, 'rename', exchange)
    assert store.cleanup(now=600) is False
    assert replacement_identity is not None
    assert displaced.is_dir()
    assert any(path.stat().st_ino == replacement_identity
               for path in tmp_path.glob('quarantine-*/slot'))


@pytest.mark.parametrize('phase', ['after-directory-capture', 'after-final-manifest', 'after-lock'])
def test_retirement_restarts_after_final_metadata_interruptions(tmp_path, monkeypatch, phase):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    rename, unlink = os.rename, os.unlink
    interrupted = False
    def capture(source, destination, **kwargs):
        nonlocal interrupted
        rename(source, destination, **kwargs)
        if phase == 'after-directory-capture' and source == store.path.name:
            interrupted = True
            raise OSError('synthetic retirement interruption')
    def erase(name, **kwargs):
        nonlocal interrupted
        unlink(name, **kwargs)
        if name == 'slot' and not interrupted:
            final_index = 1 if phase == 'after-final-manifest' else 2
            erase.calls += 1
            if phase != 'after-directory-capture' and erase.calls == final_index:
                interrupted = True
                raise OSError('synthetic final file interruption')
    erase.calls = 0
    with monkeypatch.context() as patch:
        patch.setattr(os, 'rename', capture)
        patch.setattr(os, 'unlink', erase)
        assert store.cleanup(now=600) is False
    assert interrupted
    PrivateOperationStore.recover_expired(tmp_path, now=601)
    assert not list(tmp_path.iterdir())


def test_empty_quarantine_without_journal_has_no_durable_ownership_proof(tmp_path):
    import fresh_review_quarantine as quarantine
    unknown = tmp_path / ('quarantine-' + '0' * 16 + '-' + '1' * 32)
    unknown.mkdir(mode=0o700)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert quarantine.resume_quarantines(fd, now=601) is False
    finally:
        os.close(fd)
    assert unknown.is_dir()


def test_retirement_before_capture_blocks_reopened_writer(tmp_path, monkeypatch):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    rename = os.rename
    def stop(source, destination, **kwargs):
        if source == store.path.name:
            raise OSError('synthetic interruption before directory capture')
        return rename(source, destination, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'rename', stop)
        assert store.cleanup(now=600) is False
    reopened = PrivateOperationStore.__new__(PrivateOperationStore)
    reopened._path = store.path
    reopened._directory_identity = store._directory_identity
    reopened._load()
    with pytest.raises(ObservationBlocked):
        reopened.record_evidence(evidence())
    PrivateOperationStore.recover_expired(tmp_path, now=601)
    assert not list(tmp_path.iterdir())


def test_float_directory_identity_cannot_authorize_startup_deletion(tmp_path):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    manifest = store.path / 'manifest-0.json'
    document = json.loads(manifest.read_bytes())
    document['directory'] = [float(value) for value in document['directory']]
    manifest.write_text(json.dumps(document))
    assert PrivateOperationStore.recover_expired(tmp_path, now=600) == ()
    assert manifest.exists()


def test_journal_parent_identity_requires_integer_projection(tmp_path):
    import fresh_review_quarantine as quarantine
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        record = dict(schema='mission-private-cleanup/1',
                      parent=[float(value) for value in quarantine.identity(os.fstat(fd))],
                      name='input-0', expected=[1, 1], kind='file', expires=600)
        with pytest.raises(ValueError):
            quarantine._validate(record, fd)
    finally:
        os.close(fd)


def test_maximum_selected_records_leave_recovery_metadata_budget(tmp_path):
    import fresh_review_store as module
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    record = evidence()
    for key in ('session_id', 'boot_id', 'operation_id'):
        record[key] = 'x' * 128
    for _ in range(256):
        store.record_evidence(record)
    before = {path.name: path.stat().st_size for path in store.path.iterdir()}
    with pytest.raises(ObservationBlocked):
        store.record_evidence(record)
    assert {path.name: path.stat().st_size for path in store.path.iterdir()} == before
    assert len(list(store.path.glob('manifest-*.json'))) == 257
    assert max(before[name] for name in before if name.startswith('manifest-')) <= 48 * 1024
    assert sum(before.values()) + 515 * module.JOURNAL_LIMIT <= 16 * 1024 * 1024
    assert store.cleanup(now=600) is True
    assert not list(tmp_path.iterdir())


def test_contended_operation_lock_refuses_writer_and_cleanup(tmp_path):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    reopened = PrivateOperationStore.__new__(PrivateOperationStore)
    reopened._path = store.path
    reopened._directory_identity = store._directory_identity
    reopened._load()
    before = tuple(sorted(store.path.iterdir()))
    with store._operation_lock():
        with pytest.raises(ObservationBlocked):
            reopened.record_evidence(evidence())
        assert reopened.cleanup(now=600) is False
    assert tuple(sorted(store.path.iterdir())) == before
    assert reopened.cleanup(now=600) is True


@pytest.mark.parametrize('phase', ['manifest-sync', 'input-create', 'evidence-create', 'manifest-create'])
def test_partial_write_and_failed_rollback_seal_all_additions(tmp_path, monkeypatch, phase):
    raw = b'x' * (8 * 1024 * 1024)
    (tmp_path / 'request').write_bytes(raw)
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    keeper = store.record_evidence(evidence())
    sync, unlink = os.fsync, os.unlink
    failed = False
    def fault(fd):
        nonlocal failed
        info = os.fstat(fd)
        name = ('manifest-2.json' if phase == 'manifest-create' else
                'evidence-1' if phase == 'evidence-create' else 'input-1')
        target = store.path / name
        selected = (stat.S_ISDIR(info.st_mode) and info.st_ino == store.path.stat().st_ino
                    if phase == 'manifest-sync' else target.exists() and target.stat().st_ino == info.st_ino)
        if selected and not failed:
            failed = True
            raise OSError('synthetic write sync failure')
        return sync(fd)
    def rollback(name, **kwargs):
        if name == 'slot':
            raise OSError('synthetic rollback deletion failure')
        return unlink(name, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fault)
        patch.setattr(os, 'unlink', rollback)
        with pytest.raises(ObservationBlocked):
            (store.record_evidence(evidence()) if phase == 'evidence-create'
             else store.copy_input(tmp_path, 'request', digest(raw)))
    assert failed and list(store.path.glob('quarantine-*/slot'))
    retained = sum(p.stat().st_size for p in store.path.glob('quarantine-*/slot'))
    if phase == 'manifest-create':
        assert retained > len(raw)  # Both the copy and partial metadata await cleanup.
    else:
        assert retained == (len(json.dumps(evidence(), sort_keys=True, separators=(',', ':')).encode())
                            if phase == 'evidence-create' else len(raw))
    for append in (lambda: store.copy_input(tmp_path, 'request', digest(raw)),
                   lambda: store.record_evidence(evidence())):
        with pytest.raises(ObservationBlocked):
            append()
    assert json.loads(store.read(keeper)) == evidence()
    assert store.cleanup(now=600) is True


@pytest.mark.parametrize('generation', [0, 1, 2], ids=['initial', 'older', 'latest'])
def test_startup_preserves_same_bytes_foreign_manifest_inode(tmp_path, generation):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    store.record_evidence(evidence())
    store.record_evidence(evidence())
    target = store.path / f'manifest-{generation}.json'
    original = tmp_path / 'displaced-manifest'
    target.rename(original)
    target.write_bytes(original.read_bytes())
    target.chmod(0o600)
    assert target.stat().st_ino != original.stat().st_ino
    assert PrivateOperationStore.recover_expired(tmp_path, now=600) == ()
    assert target.read_bytes() == original.read_bytes()


@pytest.mark.parametrize('bad', [None, [1], [1.0, 2], [False, 2]])
def test_startup_rejects_untyped_manifest_self_identity(tmp_path, bad):
    store = PrivateOperationStore(tmp_path, now=lambda: 0)
    target = store.path / 'manifest-0.json'
    document = json.loads(target.read_bytes())
    document['self'] = bad
    target.write_text(json.dumps(document))
    assert PrivateOperationStore.recover_expired(tmp_path, now=600) == ()
    assert target.exists()
