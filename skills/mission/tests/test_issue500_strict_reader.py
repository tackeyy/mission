"""Issue #500: strict single-link stable-identity file reader contract."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def _assert_code(path: Path, code: str, *, limit: int = 1024):
    from mission_kernel.errors import StrictReadError
    from mission_persistence.strict_reader import read_stable_bytes

    before = sorted(item.name for item in path.parent.iterdir())
    with pytest.raises(StrictReadError) as rejected:
        read_stable_bytes(path, limit=limit)
    assert rejected.value.code == code
    assert sorted(item.name for item in path.parent.iterdir()) == before


def test_read_stable_bytes_reads_exact_limit_regular_file_without_mutation(tmp_path):
    from mission_persistence.strict_reader import read_stable_bytes

    path = tmp_path / "state.json"
    path.write_bytes(b"x" * 1024)
    before = path.stat()

    assert read_stable_bytes(path, limit=1024) == b"x" * 1024
    after = path.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )


def test_read_stable_bytes_rejects_symlink_fifo_hardlink_and_oversize(tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(b'{"a":1}')
    symlink = tmp_path / "link.json"
    os.symlink(target, symlink)
    fifo = tmp_path / "review.fifo"
    os.mkfifo(fifo)
    hardlink = tmp_path / "hard.json"
    os.link(target, hardlink)
    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b"x" * 1025)

    _assert_code(symlink, "not-regular-single-link")
    _assert_code(fifo, "not-regular-single-link")
    _assert_code(hardlink, "not-regular-single-link")
    _assert_code(oversize, "record-too-large")


def test_open_uses_nofollow_and_nonblock_before_descriptor_validation(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader

    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    original_open = reader.os.open
    observed_flags = []

    def recording_open(candidate, flags):
        observed_flags.append(flags)
        return original_open(candidate, flags)

    monkeypatch.setattr(reader.os, "open", recording_open)
    assert reader.read_stable_bytes(path) == b"{}"
    assert observed_flags
    assert observed_flags[0] & os.O_NONBLOCK
    assert observed_flags[0] & os.O_NOFOLLOW


def test_final_path_is_lstat_once_and_compared_with_complete_identity(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    original_lstat = reader.os.lstat
    calls = 0

    def changed_final(candidate):
        nonlocal calls
        calls += 1
        metadata = original_lstat(candidate)
        if calls == 2:
            values = list(metadata)
            values[stat.ST_MTIME] += 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(reader.os, "lstat", changed_final)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"
    assert calls == 2


def test_final_path_disappearance_is_normalized(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    original_lstat = reader.os.lstat
    calls = 0

    def disappearing(candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FileNotFoundError(candidate)
        return original_lstat(candidate)

    monkeypatch.setattr(reader.os, "lstat", disappearing)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"


def test_truncate_during_read_is_rejected(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"abcd")
    original_read = reader.os.read
    read_calls = 0

    def short_read(fd, size):
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            return b"ab"
        if read_calls == 2:
            return b""
        return original_read(fd, size)

    monkeypatch.setattr(reader.os, "read", short_read)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"


def test_append_during_read_is_rejected(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"abcd")
    original_read = reader.os.read
    read_calls = 0

    def appended_read(fd, size):
        nonlocal read_calls
        read_calls += 1
        if read_calls == 2:
            return b"x"
        return original_read(fd, size)

    monkeypatch.setattr(reader.os, "read", appended_read)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"


def test_descriptor_fstat_drift_during_read_is_rejected(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"abcd")
    original_fstat = reader.os.fstat
    calls = 0

    def drifting_fstat(fd):
        nonlocal calls
        calls += 1
        metadata = original_fstat(fd)
        if calls != 2:
            return metadata
        fields = list(metadata)
        fields[6] += 1
        return os.stat_result(fields)

    monkeypatch.setattr(reader.os, "fstat", drifting_fstat)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"


def test_same_size_path_swap_is_rejected(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"new")
    original_read = reader.os.read
    swapped = False

    def swap_after_read(fd, size):
        nonlocal swapped
        content = original_read(fd, size)
        if content and not swapped:
            os.replace(replacement, path)
            swapped = True
        return content

    monkeypatch.setattr(reader.os, "read", swap_after_read)
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "identity-changed"


def test_missing_nofollow_is_fail_closed(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as reader
    from mission_kernel.errors import StrictReadError

    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    monkeypatch.delattr(reader.os, "O_NOFOLLOW")
    with pytest.raises(StrictReadError) as rejected:
        reader.read_stable_bytes(path)
    assert rejected.value.code == "not-regular-single-link"
