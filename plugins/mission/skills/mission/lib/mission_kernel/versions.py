"""Schema version classification for mission documents."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import MissionStateDecodeError
from .model import SchemaOrigin


def read_schema_version(document: Mapping[str, Any], *, max_reader_version: int) -> SchemaOrigin:
    if "schema_version" not in document:
        return SchemaOrigin.MISSING
    value = document["schema_version"]
    if type(value) is not int:
        raise MissionStateDecodeError(
            "schema-version-type", "$.schema_version", "schema_version must be an integer"
        )
    if value < 1 or value > max_reader_version:
        raise MissionStateDecodeError(
            "unsupported-schema-version",
            "$.schema_version",
            f"schema_version {value} is unsupported",
        )
    return {
        1: SchemaOrigin.V1,
        2: SchemaOrigin.V2,
        3: SchemaOrigin.V3,
        4: SchemaOrigin.V4,
        5: SchemaOrigin.V5,
    }[value]
