"""Issue #500: strict JSON byte codec and schema-version primitive contract."""

from __future__ import annotations

import hashlib

import pytest


def _assert_decode_error(payload: bytes, code: str, path: str = "$"):
    from mission_kernel.errors import MissionStateDecodeError
    from mission_kernel.json_codec import decode_json_object

    before = hashlib.sha256(payload).digest()
    with pytest.raises(MissionStateDecodeError) as rejected:
        decode_json_object(payload)
    assert rejected.value.code == code
    assert rejected.value.json_path == path
    assert hashlib.sha256(payload).digest() == before


@pytest.mark.parametrize(
    "payload",
    [b'{"a":1,"a":2}', b'{"outer":{"a":1,"a":2}}'],
    ids=["root", "nested"],
)
def test_decode_json_object_rejects_duplicate_keys(payload):
    _assert_decode_error(payload, "duplicate-json-key")


def test_decode_json_object_rejects_invalid_utf8():
    _assert_decode_error(b"\xff", "invalid-utf8")


@pytest.mark.parametrize(
    "payload",
    [b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}'],
    ids=["nan", "pos-inf", "neg-inf", "overflow"],
)
def test_decode_json_object_rejects_non_finite_numbers(payload):
    _assert_decode_error(payload, "non-finite-number")


@pytest.mark.parametrize(
    "payload",
    [b'{"a":1} trailing', b'{"a":1}{"b":2}'],
    ids=["prose", "second-document"],
)
def test_decode_json_object_rejects_trailing_data(payload):
    _assert_decode_error(payload, "trailing-json-data")


@pytest.mark.parametrize("payload", [b"[]", b"null", b'"value"'])
def test_decode_json_object_rejects_root_non_object(payload):
    _assert_decode_error(payload, "root-not-object")


def test_decode_json_object_accepts_exact_limit_and_rejects_one_byte_more():
    from mission_kernel.json_codec import decode_json_object

    exact = b'{"a":"' + b"x" * (4 * 1024 * 1024 - len(b'{"a":""}')) + b'"}'
    assert len(exact) == 4 * 1024 * 1024
    assert decode_json_object(exact)
    _assert_decode_error(exact + b" ", "record-too-large")


@pytest.mark.parametrize(
    "version",
    [True, "4", 4.0, None],
    ids=["bool", "string", "float", "null"],
)
def test_schema_version_rejects_non_exact_int(version):
    from mission_kernel.errors import MissionStateDecodeError
    from mission_kernel.versions import read_schema_version

    with pytest.raises(MissionStateDecodeError) as rejected:
        read_schema_version({"schema_version": version}, max_reader_version=5)
    assert rejected.value.code == "schema-version-type"
    assert rejected.value.json_path == "$.schema_version"


@pytest.mark.parametrize("version", [0, -1, 6])
def test_schema_version_rejects_out_of_range(version):
    from mission_kernel.errors import MissionStateDecodeError
    from mission_kernel.versions import read_schema_version

    with pytest.raises(MissionStateDecodeError) as rejected:
        read_schema_version({"schema_version": version}, max_reader_version=5)
    assert rejected.value.code == "unsupported-schema-version"
    assert rejected.value.json_path == "$.schema_version"


def test_error_constructor_uses_code_path_detail_order():
    from mission_kernel.errors import MissionStateDecodeError

    error = MissionStateDecodeError("invalid-value", "$.control.phase", "phase is invalid")
    assert error.code == "invalid-value"
    assert error.json_path == "$.control.phase"
    assert error.detail == "phase is invalid"

