"""Actual-filesystem contracts for a caller-bound cleanup anchor."""
import json
import os
from pathlib import Path

import pytest

from fresh_review_observation import ObservationBlocked
from fresh_review_quarantine import initialize_anchor, open_anchor, AnchorBinding


def test_explicit_initialization_can_reopen_only_with_persisted_binding(tmp_path):
    binding = initialize_anchor(tmp_path)
    persisted = json.loads(json.dumps(binding.to_dict()))
    with open_anchor(tmp_path, AnchorBinding.from_dict(persisted)) as anchor:
        assert anchor.binding == binding
    assert set(tmp_path.iterdir()) == {tmp_path / binding.anchor_name}
    assert set((tmp_path / binding.anchor_name).iterdir()) == {tmp_path / binding.anchor_name / 'anchor.json'}


def test_normal_cleanup_captures_source_and_retains_only_fixed_anchor(tmp_path):
    source = tmp_path / 'input-0'
    source.write_bytes(b'owned bytes')
    source.chmod(0o600)
    expected = (source.stat().st_dev, source.stat().st_ino)
    binding = initialize_anchor(tmp_path)
    with open_anchor(tmp_path, binding) as anchor:
        assert anchor.remove_owned('input-0', expected, expires=600, now=600) is True
        assert anchor.recover(now=601) is True
    assert not source.exists()
    assert list((tmp_path / binding.anchor_name).iterdir()) == [tmp_path / binding.anchor_name / 'anchor.json']


def owned(parent, name='input-0'):
    path = parent / name
    path.write_bytes(b'owned')
    path.chmod(0o600)
    return path, (path.stat().st_dev, path.stat().st_ino)


def test_public_quarantine_basename_replacement_is_never_retired(tmp_path, monkeypatch):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    rmdir = os.rmdir
    replacements = []
    def exchange(name, **kwargs):
        if name.startswith('quarantine-'):
            replacement = tmp_path / name
            replacement.mkdir(mode=0o700)
            replacements.append((replacement, replacement.stat().st_ino))
        return rmdir(name, **kwargs)
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rmdir', exchange)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is True
    assert replacements and all(path.stat().st_ino == inode for path, inode in replacements)


def test_public_anchor_exchange_during_retirement_preserves_both_directories(tmp_path, monkeypatch):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    original = tmp_path / 'original-anchor'
    rmdir = os.rmdir
    replacement_inode = None
    def exchange(name, **kwargs):
        nonlocal replacement_inode
        if name.startswith('quarantine-'):
            (tmp_path / binding.anchor_name).rename(original)
            (tmp_path / binding.anchor_name).mkdir(mode=0o700)
            replacement_inode = (tmp_path / binding.anchor_name).stat().st_ino
        return rmdir(name, **kwargs)
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rmdir', exchange)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is True
    assert original.stat().st_ino == binding.anchor[1]
    assert (original / 'anchor.json').stat().st_ino == binding.marker[1]
    assert (tmp_path / binding.anchor_name).stat().st_ino == replacement_inode
    with pytest.raises(ObservationBlocked):
        with open_anchor(tmp_path, binding):
            pass


@pytest.mark.parametrize('stage', ['before-capture', 'after-capture', 'after-slot', 'after-journal'])
def test_restart_reuses_journal_and_retains_unbound_empty_quarantine(tmp_path, monkeypatch, stage):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    rename, unlink = os.rename, os.unlink
    fired = False
    def capture(name, destination, **kwargs):
        nonlocal fired
        if name == source.name:
            if stage == 'before-capture':
                fired = True
                raise OSError('synthetic before capture')
            rename(name, destination, **kwargs)
            if stage == 'after-capture':
                fired = True
                raise OSError('synthetic after capture')
            return
        return rename(name, destination, **kwargs)
    def erase(name, **kwargs):
        nonlocal fired
        unlink(name, **kwargs)
        if (stage, name) in (('after-slot', 'slot'), ('after-journal', 'journal.json')):
            fired = True
            raise OSError('synthetic private erase interruption')
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rename', capture)
        patch.setattr(os, 'unlink', erase)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
    assert fired
    with open_anchor(tmp_path, binding) as anchor:
        before = tuple(sorted((tmp_path / binding.anchor_name).rglob('*')))
        assert anchor.recover(now=601) is (stage != 'after-journal')
        assert anchor.recover(now=602) is (stage != 'after-journal')
        if stage == 'after-journal':
            assert tuple(sorted((tmp_path / binding.anchor_name).rglob('*'))) == before
        else:
            assert not source.exists()


@pytest.mark.parametrize('target', ['parent', 'anchor', 'marker', 'missing-marker', 'missing-anchor'])
def test_reopen_never_adopts_replacements_or_allocates_on_missing_binding(tmp_path, target):
    binding = initialize_anchor(tmp_path)
    anchor = tmp_path / binding.anchor_name
    if target == 'parent':
        original = tmp_path.with_name(tmp_path.name + '-original')
        tmp_path.rename(original)
        tmp_path.mkdir(mode=0o700)
    elif target in ('anchor', 'missing-anchor'):
        anchor.rename(tmp_path / 'original-anchor')
        if target == 'anchor':
            anchor.mkdir(mode=0o700)
    else:
        marker = anchor / 'anchor.json'
        marker.rename(anchor / 'original-marker')
        if target == 'marker':
            marker.write_bytes((anchor / 'original-marker').read_bytes())
            marker.chmod(0o600)
    before = tuple(sorted(tmp_path.rglob('*')))
    for _ in range(2):
        with pytest.raises(ObservationBlocked):
            with open_anchor(tmp_path, binding):
                pass
    assert tuple(sorted(tmp_path.rglob('*'))) == before


def test_missing_binding_and_repeat_bootstrap_do_not_allocate(tmp_path):
    from fresh_review_observation import ObservationBlocked
    binding = initialize_anchor(tmp_path)
    before = tuple(sorted(tmp_path.rglob('*')))
    with pytest.raises(ObservationBlocked):
        initialize_anchor(tmp_path)
    with pytest.raises(ObservationBlocked):
        with open_anchor(tmp_path, None):
            pass
    assert tuple(sorted(tmp_path.rglob('*'))) == before
    assert binding.anchor_name == 'cleanup-anchor'


def test_inner_anchor_is_retired_only_after_verified_outer_capture(tmp_path, monkeypatch):
    operation = tmp_path / 'operation-1'
    operation.mkdir(mode=0o700)
    inner = initialize_anchor(operation)
    outer = initialize_anchor(tmp_path)
    expected = (operation.stat().st_dev, operation.stat().st_ino)
    rmdir = os.rmdir
    checks = []
    def retire(name, **kwargs):
        if name == inner.anchor_name:
            assert not operation.exists()
            assert (os.fstat(kwargs['dir_fd']).st_dev, os.fstat(kwargs['dir_fd']).st_ino) == expected
            checks.append(name)
        return rmdir(name, **kwargs)
    with open_anchor(tmp_path, outer) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rmdir', retire)
        assert anchor.remove_owned(operation.name, expected, kind='directory',
                                   inner=inner, expires=600, now=600) is True
    assert checks == [inner.anchor_name]
    assert not operation.exists()
    assert list((tmp_path / outer.anchor_name).iterdir()) == [tmp_path / outer.anchor_name / 'anchor.json']


@pytest.mark.parametrize('target', ['marker-mode', 'anchor-mode', 'marker-link', 'marker-symlink', 'anchor-symlink'])
def test_open_rejects_unsafe_owner_private_registration(tmp_path, target):
    binding = initialize_anchor(tmp_path)
    directory = tmp_path / binding.anchor_name
    marker = directory / 'anchor.json'
    if target == 'marker-mode':
        marker.chmod(0o644)
    elif target == 'anchor-mode':
        directory.chmod(0o755)
    elif target == 'marker-link':
        os.link(marker, directory / 'linked')
    elif target == 'marker-symlink':
        marker.rename(directory / 'original')
        marker.symlink_to('original')
    else:
        directory.rename(tmp_path / 'original')
        directory.symlink_to('original', target_is_directory=True)
    before = tuple(sorted(tmp_path.rglob('*')))
    with pytest.raises(ObservationBlocked):
        with open_anchor(tmp_path, binding):
            pass
    assert tuple(sorted(tmp_path.rglob('*'))) == before


@pytest.mark.parametrize('field', ['unknown', 'parent-float', 'self-bool', 'duplicate'])
def test_marker_closed_schema_rejects_noncanonical_binding(tmp_path, field):
    binding = initialize_anchor(tmp_path)
    marker = tmp_path / binding.anchor_name / 'anchor.json'
    record = json.loads(marker.read_bytes())
    if field == 'unknown':
        record['private'] = 'not accepted'
    elif field == 'parent-float':
        record['parent'][0] = float(record['parent'][0])
    elif field == 'self-bool':
        record['self'][0] = True
    raw = json.dumps(record)
    if field == 'duplicate':
        raw = raw[:-1] + ',"schema":"mission-private-anchor/1"}'
    marker.write_text(raw)
    with pytest.raises(ObservationBlocked):
        with open_anchor(tmp_path, binding):
            pass
    assert marker.read_text() == raw


def test_source_exchange_retains_unknown_slot_and_retries_without_growth(tmp_path, monkeypatch):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    displaced = tmp_path / 'displaced'
    rename = os.rename
    def exchange(name, destination, **kwargs):
        if name == source.name:
            source.rename(displaced)
            source.write_bytes(b'unknown replacement')
            source.chmod(0o600)
        return rename(name, destination, **kwargs)
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rename', exchange)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
    slots = list((tmp_path / binding.anchor_name).glob('quarantine-*/slot'))
    assert len(slots) == 1 and slots[0].read_bytes() == b'unknown replacement'
    assert displaced.read_bytes() == b'owned'
    before = tuple(sorted((tmp_path / binding.anchor_name).rglob('*')))
    with open_anchor(tmp_path, binding) as anchor:
        for _ in range(3):
            assert anchor.remove_owned(source.name, expected, expires=600, now=601) is False
            assert anchor.recover(now=601) is False
    assert tuple(sorted((tmp_path / binding.anchor_name).rglob('*'))) == before


def test_expiry_boolean_flags_and_real_flock_contention_preserve_source(tmp_path):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    with open_anchor(tmp_path, binding) as anchor:
        assert anchor.remove_owned(source.name, expected, expires=600, now=599) is False
        with pytest.raises(ObservationBlocked):
            anchor.remove_owned(source.name, expected, expires=600, now=599, force='false')
        with pytest.raises(ObservationBlocked):
            with open_anchor(tmp_path, binding):
                pass
        assert source.read_bytes() == b'owned'
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is True


def test_actual_quarantine_count_cap_refuses_new_allocation(tmp_path):
    from fresh_review_quarantine import QUARANTINE_LIMIT
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    directory = tmp_path / binding.anchor_name
    for index in range(QUARANTINE_LIMIT):
        (directory / f'quarantine-{index:016x}-{index:032x}').mkdir(mode=0o700)
    before = set(directory.iterdir())
    with open_anchor(tmp_path, binding) as anchor:
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
        assert anchor.recover(now=601) is False
    assert set(directory.iterdir()) == before
    assert source.read_bytes() == b'owned'


def test_partial_journal_write_is_bounded_and_never_reallocated(tmp_path, monkeypatch):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    write = os.write
    def partial(fd, raw):
        write(fd, raw[:9])
        raise OSError('synthetic partial journal write')
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'write', partial)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
    before = tuple(sorted((tmp_path / binding.anchor_name).rglob('*')))
    with open_anchor(tmp_path, binding) as anchor:
        for _ in range(3):
            assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
            assert anchor.recover(now=601) is False
    assert tuple(sorted((tmp_path / binding.anchor_name).rglob('*'))) == before
    assert source.read_bytes() == b'owned'


def test_bootstrap_sync_failure_without_handoff_retains_unknown_fixed_anchor(tmp_path, monkeypatch):
    def fault(fd):
        raise OSError('synthetic bootstrap durability failure')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fault)
        with pytest.raises(ObservationBlocked):
            initialize_anchor(tmp_path)
    before = tuple(sorted(tmp_path.rglob('*')))
    for _ in range(2):
        with pytest.raises(ObservationBlocked):
            initialize_anchor(tmp_path)
        with pytest.raises(ObservationBlocked):
            with open_anchor(tmp_path, None):
                pass
    assert tuple(sorted(tmp_path.rglob('*'))) == before


@pytest.mark.parametrize('stage', ['marker-deleted', 'anchor-deleted'])
def test_inner_retirement_restart_keeps_binding_after_marker_loss(tmp_path, monkeypatch, stage):
    operation = tmp_path / 'operation-1'
    operation.mkdir(mode=0o700)
    inner = initialize_anchor(operation)
    outer = initialize_anchor(tmp_path)
    expected = (operation.stat().st_dev, operation.stat().st_ino)
    unlink, rmdir = os.unlink, os.rmdir
    def erase(name, **kwargs):
        unlink(name, **kwargs)
        if stage == 'marker-deleted' and name == 'anchor.json':
            raise OSError('synthetic interrupted inner retirement')
    def retire(name, **kwargs):
        rmdir(name, **kwargs)
        if stage == 'anchor-deleted' and name == inner.anchor_name:
            raise OSError('synthetic interrupted inner retirement')
    with open_anchor(tmp_path, outer) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', erase)
        patch.setattr(os, 'rmdir', retire)
        assert anchor.remove_owned(operation.name, expected, kind='directory',
                                   inner=inner, expires=600, now=600) is False
    with open_anchor(tmp_path, outer) as anchor:
        assert anchor.recover(now=601) is True
    assert not operation.exists()


def test_foreign_inner_binding_retains_captured_directory_and_does_not_adopt(tmp_path):
    operation = tmp_path / 'operation-1'
    operation.mkdir(mode=0o700)
    inner = initialize_anchor(operation)
    outer = initialize_anchor(tmp_path)
    registered = operation / inner.anchor_name
    registered.rename(operation / 'original-anchor')
    registered.mkdir(mode=0o700)
    replacement = registered.stat().st_ino
    expected = (operation.stat().st_dev, operation.stat().st_ino)
    with open_anchor(tmp_path, outer) as anchor:
        assert anchor.remove_owned(operation.name, expected, kind='directory',
                                   inner=inner, expires=600, now=600) is False
        assert anchor.recover(now=601) is False
    slot = next((tmp_path / outer.anchor_name).glob('quarantine-*/slot'))
    assert (slot / inner.anchor_name).stat().st_ino == replacement
    assert (slot / 'original-anchor').stat().st_ino == inner.anchor[1]


def test_captured_inner_recovery_does_not_recurse_into_another_inner_anchor(tmp_path, monkeypatch):
    operation = tmp_path / 'operation-1'
    operation.mkdir(mode=0o700)
    child = operation / 'operation-2'
    child.mkdir(mode=0o700)
    grandchild = initialize_anchor(child)
    inner = initialize_anchor(operation)
    outer = initialize_anchor(tmp_path)
    def stop(*args, **kwargs):
        raise OSError('synthetic capture interruption')
    with open_anchor(operation, inner) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rename', stop)
        assert anchor.remove_owned(child.name, (child.stat().st_dev, child.stat().st_ino),
                                   kind='directory', inner=grandchild, expires=600, now=600) is False
    with open_anchor(tmp_path, outer) as anchor:
        assert anchor.remove_owned(operation.name, inner.parent, kind='directory',
                                   inner=inner, expires=600, now=600) is False


def test_normal_repetition_and_known_missing_leave_constant_root_residue(tmp_path):
    binding = initialize_anchor(tmp_path)
    marker = tmp_path / binding.anchor_name / 'anchor.json'
    with open_anchor(tmp_path, binding) as anchor:
        for index in range(4):
            source, expected = owned(tmp_path, f'input-{index}')
            assert anchor.remove_owned(source.name, expected, expires=600, now=600) is True
            assert anchor.remove_owned(source.name, expected, expires=600, now=601) is True
            assert anchor.recover(now=601) is True
            assert list((tmp_path / binding.anchor_name).iterdir()) == [marker]
    assert marker.stat().st_ino == binding.marker[1]


def test_noncanonical_quarantine_name_is_unknown_even_with_valid_journal(tmp_path, monkeypatch):
    source, expected = owned(tmp_path)
    binding = initialize_anchor(tmp_path)
    def stop(*args, **kwargs):
        raise OSError('synthetic before capture')
    with open_anchor(tmp_path, binding) as anchor, monkeypatch.context() as patch:
        patch.setattr(os, 'rename', stop)
        assert anchor.remove_owned(source.name, expected, expires=600, now=600) is False
    pending = next((tmp_path / binding.anchor_name).glob('quarantine-*'))
    unknown = pending.with_name(pending.name.rsplit('-', 1)[0] + '-unknown')
    pending.rename(unknown)
    with open_anchor(tmp_path, binding) as anchor:
        assert anchor.remove_owned(source.name, expected, expires=600, now=601) is False
    assert source.read_bytes() == b'owned' and unknown.is_dir()
