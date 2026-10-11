"""Model-free boundary tests, never evidence of a native host's behavior."""
import hashlib

import pytest

from fresh_review_observation import ContextManifest, SourcePolicy, ObservationBlocked, QueryIdentity


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def test_manifest_reads_pinned_policy_and_refuses_unlisted_source(tmp_path):
    raw = b'Fixed review policy.\n'
    (tmp_path / 'policy.txt').write_bytes(raw)
    manifest = ContextManifest((SourcePolicy('policy.txt', digest(raw)),))
    manifest.verify(tmp_path, enumerate_sources=lambda: ('policy.txt',), hook_outputs={})
    with pytest.raises(ObservationBlocked):
        manifest.verify(tmp_path, enumerate_sources=lambda: ('policy.txt', 'memory.txt'), hook_outputs={})


def test_hook_requires_closed_static_dependencies_and_observed_fixed_output(tmp_path):
    (tmp_path / 'policy.txt').write_bytes(b'policy')
    (tmp_path / 'hook.py').write_bytes(b'fixed hook implementation')
    manifest = ContextManifest((
        SourcePolicy('policy.txt', digest(b'policy')),
        SourcePolicy('hook.py', digest(b'fixed hook implementation'), kind='hook',
                     inputs=('policy.txt',), output_digest=digest(b'fixed context')),
    ))
    enumerate_sources = lambda: ('policy.txt', 'hook.py')
    manifest.verify(tmp_path, enumerate_sources=enumerate_sources,
                    hook_outputs={'hook.py': b'fixed context'})
    for outputs in ({}, {'hook.py': b'parent summary'}, {'hook.py': 'fixed context'}):
        with pytest.raises(ObservationBlocked):
            manifest.verify(tmp_path, enumerate_sources=enumerate_sources, hook_outputs=outputs)


@pytest.mark.parametrize('sources', [
    (SourcePolicy('policy.txt', digest(b'policy'), kind='dynamic-memory'),),
    (SourcePolicy('policy.txt', digest(b'policy'), inputs=('parent-summary',)),),
    (SourcePolicy('policy.txt', digest(b'policy'), kind='hook', inputs=('environment',),
                  output_digest=digest(b'')),),
    (SourcePolicy('policy.txt', digest(b'policy'), kind='hook', inputs=('policy.txt',),
                  output_digest=digest(b'')),),
    (SourcePolicy('policy.txt', digest(b'policy')), SourcePolicy('policy.txt', digest(b'policy'))),
])
def test_manifest_cannot_label_dynamic_or_unclosed_inputs_as_static(sources, tmp_path):
    (tmp_path / 'policy.txt').write_bytes(b'policy')
    with pytest.raises(ObservationBlocked):
        ContextManifest(sources).verify(tmp_path, enumerate_sources=lambda: ('policy.txt',),
                                        hook_outputs={'policy.txt': b''} if sources[0].kind == 'hook' else {})


def test_query_identity_requires_both_native_events_from_its_owner():
    owner = object()
    identity = QueryIdentity(owner)
    with pytest.raises(ObservationBlocked):
        identity.observe()
    identity.session_start(owner, {'source': 'startup', 'session_id': 'native-parent'})
    with pytest.raises(ObservationBlocked):
        identity.observe()
    identity.init(owner, {'session_id': 'native-parent'})
    assert identity.observe() == 'native-parent'
    with pytest.raises(ObservationBlocked):
        identity.init(object(), {'session_id': 'native-parent'})
    with pytest.raises(ObservationBlocked):
        identity.observe()


@pytest.mark.parametrize('fault', ['changed', 'symlink', 'missing', 'duplicate-enumeration', 'unobserved'])
def test_context_observation_refuses_drift_and_unobservable_sources(tmp_path, fault):
    raw = b'policy'
    policy = tmp_path / 'policy.txt'
    policy.write_bytes(raw)
    if fault == 'changed':
        policy.write_bytes(b'parent summary')
    elif fault == 'symlink':
        policy.unlink()
        (tmp_path / 'other.txt').write_bytes(raw)
        policy.symlink_to('other.txt')
    elif fault == 'missing':
        policy.unlink()
    def enumerate_sources():
        if fault == 'unobserved':
            raise OSError('unavailable')
        return ('policy.txt', 'policy.txt') if fault == 'duplicate-enumeration' else ('policy.txt',)
    with pytest.raises(ObservationBlocked):
        ContextManifest((SourcePolicy('policy.txt', digest(raw)),)).verify(
            tmp_path, enumerate_sources=enumerate_sources, hook_outputs={})


@pytest.mark.parametrize('first,second', [
    ({'source': 'resume', 'session_id': 'p'}, {'session_id': 'p'}),
    ({'source': 'fork', 'session_id': 'p'}, {'session_id': 'p'}),
    ({'source': 'startup', 'session_id': 'p'}, {'session_id': 'other'}),
    ({'source': 'startup', 'session_id': 'p', 'fresh': True}, {'session_id': 'p'}),
    ({'source': 'startup', 'session_id': ''}, {'session_id': ''}),
])
def test_native_identity_rejection_is_sticky(first, second):
    owner = object()
    identity = QueryIdentity(owner)
    with pytest.raises(ObservationBlocked):
        identity.session_start(owner, first)
        identity.init(owner, second)
    with pytest.raises(ObservationBlocked):
        identity.observe()


@pytest.mark.parametrize('duplicate', ['session_start', 'init'])
def test_duplicate_native_event_invalidates_previously_observed_identity(duplicate):
    owner = object()
    identity = QueryIdentity(owner)
    start, init = {'source': 'startup', 'session_id': 'p'}, {'session_id': 'p'}
    identity.init(owner, init)
    identity.session_start(owner, start)
    assert identity.observe() == 'p'
    with pytest.raises(ObservationBlocked):
        getattr(identity, duplicate)(owner, start if duplicate == 'session_start' else init)
    with pytest.raises(ObservationBlocked):
        identity.observe()
