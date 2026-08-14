"""Publish transaction diagnostics stay precise enough for post-failure triage."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("mission_state_publish_diagnostics", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _clone_stat(metadata, **changes):
    values = list(metadata)
    index_by_name = {
        "st_mode": 0,
        "st_ino": 1,
        "st_dev": 2,
        "st_nlink": 3,
        "st_uid": 4,
        "st_gid": 5,
        "st_size": 6,
        "st_atime": 7,
        "st_mtime": 8,
        "st_ctime": 9,
    }
    for name, value in changes.items():
        values[index_by_name[name]] = value
    return os.stat_result(values)


def _assert_no_path_markers(message):
    assert "/" not in message
    assert ".tmp" not in message


def test_publish_identity_detail_omits_missing_values_and_paths():
    module = _load_mission_state()

    metadata = os.stat(os.fspath(Path(__file__).resolve().parent))
    detail = module._publish_identity_detail(metadata, metadata, reason="size")

    assert detail == (
        "reason=size "
        f"expected_dev={metadata.st_dev} "
        f"expected_ino={metadata.st_ino} "
        f"expected_mode={oct(metadata.st_mode)} "
        f"expected_nlink={metadata.st_nlink} "
        f"observed_dev={metadata.st_dev} "
        f"observed_ino={metadata.st_ino} "
        f"observed_mode={oct(metadata.st_mode)} "
        f"observed_nlink={metadata.st_nlink}"
    )
    _assert_no_path_markers(detail)


@pytest.mark.parametrize(
    "field, expected_reason",
    [
        ("st_dev", "dev"),
        ("st_ino", "ino"),
        ("st_mode", "mode"),
        ("st_nlink", "nlink"),
    ],
    ids=["dev", "ino", "mode", "nlink"],
)
def test_output_publish_changed_reports_first_inode_mismatch(
    tmp_path, monkeypatch, field, expected_reason,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    content = b"replacement-output\n"
    original_stat = module.os.stat

    def fake_stat(path, *args, **kwargs):
        metadata = original_stat(path, *args, **kwargs)
        if path == out.name and kwargs.get("follow_symlinks", True) is False:
            return _clone_stat(metadata, **{field: getattr(metadata, field) + 1})
        return metadata

    monkeypatch.setattr(module.os, "stat", fake_stat)

    with pytest.raises(ValueError) as stopped:
        module._publish_output_transaction(out, content)

    message = str(stopped.value)
    assert message.startswith("output publish changed: reason=" + expected_reason)
    _assert_no_path_markers(message)
    assert f"expected_size={len(content)}" in message
    assert "observed_size=" in message


def test_output_temporary_file_changed_reports_identity_detail(tmp_path, monkeypatch):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    content = b"replacement-output\n"
    temp_name = {"value": None}
    original_write_temp = module._write_temp_at
    original_stat = module.os.stat

    def tracked_write_temp(directory_fd, name, payload):
        temporary, metadata = original_write_temp(directory_fd, name, payload)
        temp_name["value"] = temporary
        return temporary, metadata

    def fake_stat(path, *args, **kwargs):
        metadata = original_stat(path, *args, **kwargs)
        if temp_name["value"] is not None and path == temp_name["value"]:
            return _clone_stat(metadata, st_size=metadata.st_size + 1)
        return metadata

    monkeypatch.setattr(module, "_write_temp_at", tracked_write_temp)
    monkeypatch.setattr(module.os, "stat", fake_stat)

    with pytest.raises(ValueError) as stopped:
        module._publish_output_transaction(out, content)

    message = str(stopped.value)
    assert message.startswith("output temporary file changed: reason=size")
    _assert_no_path_markers(message)
    assert f"expected_size={len(content)}" in message
    assert "observed_size=" in message


def test_output_changed_during_publish_reports_first_identity_mismatch(
    tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    previous = b"previous-output\n"
    content = b"replacement-output\n"
    out.write_bytes(previous)
    stale_size = len(previous) + 1
    stale_identity = _clone_stat(
        module.os.stat(out, follow_symlinks=False),
        st_size=stale_size,
    )

    def fake_read_review_archive_at(directory_fd, name):
        assert name == out.name
        return previous, module._stat_identity(stale_identity)

    monkeypatch.setattr(module, "_read_review_archive_at", fake_read_review_archive_at)

    with pytest.raises(ValueError) as stopped:
        module._publish_output_transaction(out, content)

    message = str(stopped.value)
    assert message.startswith("output changed during publish: reason=size")
    _assert_no_path_markers(message)
    assert f"expected_size={stale_size}" in message
    assert f"observed_size={len(previous)}" in message


def test_publish_directory_changed_reports_directory_identity_detail(
    tmp_path, monkeypatch,
):
    module = _load_mission_state()
    out = tmp_path / "score.json"
    content = b"replacement-output\n"
    original_fstat = module.os.fstat
    original_lstat = Path.lstat
    opened_fd = {"value": None}

    def fake_open_publish_directory(path):
        path.mkdir(parents=True, exist_ok=True)
        flags = module.os.O_RDONLY | getattr(module.os, "O_DIRECTORY", 0) | getattr(module.os, "O_NOFOLLOW", 0)
        directory_fd = module.os.open(os.fspath(path), flags)
        opened = original_fstat(directory_fd)
        named = original_lstat(path)
        identity = module._directory_identity(opened)
        assert module.stat.S_ISDIR(opened.st_mode)
        assert module._directory_identity(named) == identity
        opened_fd["value"] = directory_fd
        return directory_fd, (identity[0], identity[1], identity[2] + 1)

    def fake_fstat(fd):
        metadata = original_fstat(fd)
        if fd == opened_fd["value"]:
            return _clone_stat(
                metadata,
                st_dev=metadata.st_dev + 11,
                st_ino=metadata.st_ino + 11,
            )
        return metadata

    def fake_lstat(self):
        metadata = original_lstat(self)
        if self == out.parent.resolve():
            return _clone_stat(
                metadata,
                st_dev=metadata.st_dev + 11,
                st_ino=metadata.st_ino + 11,
            )
        return metadata

    monkeypatch.setattr(module, "_open_publish_directory", fake_open_publish_directory)
    monkeypatch.setattr(module.os, "fstat", fake_fstat)
    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError) as stopped:
        module._publish_output_transaction(out, content)

    message = str(stopped.value)
    assert message.startswith("publish directory changed: reason=directory-opened")
    _assert_no_path_markers(message)
    assert "expected_dev=" in message
    assert "expected_ino=" in message
    assert "expected_mode=" in message
    assert "opened_dev=" in message
    assert "opened_ino=" in message
    assert "opened_mode=" in message
    assert "named_dev=" in message
    assert "named_ino=" in message
    assert "named_mode=" in message


def test_open_publish_directory_reports_not_a_dir_detail(tmp_path, monkeypatch):
    module = _load_mission_state()
    directory = tmp_path / "publish"
    original_open = module.os.open
    original_fstat = module.os.fstat
    opened_fd = {"value": None}

    def tracked_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened_fd["value"] = fd
        return fd

    def fake_fstat(fd):
        metadata = original_fstat(fd)
        if fd == opened_fd["value"]:
            return _clone_stat(metadata, st_mode=(metadata.st_mode & ~0o170000) | 0o100000)
        return metadata

    monkeypatch.setattr(module.os, "open", tracked_open)
    monkeypatch.setattr(module.os, "fstat", fake_fstat)

    with pytest.raises(ValueError) as stopped:
        module._open_publish_directory(directory)

    message = str(stopped.value)
    assert message.startswith("publish directory changed: reason=not-a-dir")
    _assert_no_path_markers(message)
    assert "expected_dev=" in message
    assert "opened_dev=" in message
    assert "named_dev=" in message


def test_open_publish_directory_reports_pre_open_detail(tmp_path, monkeypatch):
    module = _load_mission_state()
    directory = tmp_path / "publish"
    original_lstat = Path.lstat

    def fake_lstat(self):
        metadata = original_lstat(self)
        if self == directory:
            return _clone_stat(
                metadata,
                st_dev=metadata.st_dev + 17,
                st_ino=metadata.st_ino + 17,
                st_mode=(metadata.st_mode & ~0o170000) | 0o100000,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError) as stopped:
        module._open_publish_directory(directory)

    message = str(stopped.value)
    assert message.startswith("publish directory changed: reason=pre-open")
    _assert_no_path_markers(message)
    assert "expected_dev=" in message
    assert "opened_dev=" in message
    assert "named_dev=" in message
