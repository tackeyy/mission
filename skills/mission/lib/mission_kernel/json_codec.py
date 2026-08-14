"""Strict JSON byte codec for mission documents."""

from __future__ import annotations

import json
import math
from typing import Any

from .errors import MissionStateDecodeError
from .model import FrozenJsonObject, FrozenJsonValue, thaw_json_value

STATE_LIMIT = 4 * 1024 * 1024


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise MissionStateDecodeError("duplicate-json-key", "$", f"duplicate key {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _parse_constant(value: str) -> Any:
    raise MissionStateDecodeError("non-finite-number", "$", value)


def _freeze(value: Any) -> FrozenJsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MissionStateDecodeError("non-finite-number", "$", repr(value))
        return value
    if isinstance(value, dict):
        return FrozenJsonObject(tuple((key, _freeze(inner)) for key, inner in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    raise MissionStateDecodeError("root-not-object", "$", f"unsupported JSON value {type(value).__name__}")


def freeze_json_value(value: Any) -> FrozenJsonValue:
    return _freeze(value)


def decode_json_object(source: bytes, *, limit: int = STATE_LIMIT) -> FrozenJsonObject:
    if len(source) > limit:
        raise MissionStateDecodeError("record-too-large", "$", f"document exceeds {limit} bytes")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MissionStateDecodeError("invalid-utf8", "$", "input is not UTF-8") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_parse_constant,
        )
    except MissionStateDecodeError:
        raise
    except json.JSONDecodeError as exc:
        if exc.msg == "Extra data":
            raise MissionStateDecodeError("trailing-json-data", "$", exc.msg) from exc
        raise MissionStateDecodeError("root-not-object", "$", exc.msg) from exc
    if not isinstance(document, dict):
        raise MissionStateDecodeError("root-not-object", "$", "top-level JSON value must be an object")
    frozen = _freeze(document)
    assert isinstance(frozen, FrozenJsonObject)
    return frozen


def encode_json_value(value: FrozenJsonValue) -> bytes:
    return json.dumps(
        thaw_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def encode_json_object(document: FrozenJsonObject) -> bytes:
    return encode_json_value(document)


def thaw_json_object(document: FrozenJsonObject) -> dict[str, Any]:
    return document.thaw()
