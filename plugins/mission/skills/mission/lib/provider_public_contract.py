"""Portable public-state hygiene for specialist provider records."""

from __future__ import annotations

import re
from typing import Any


PORTABLE_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
OPAQUE_PROVIDER_ID = re.compile(r"provider:sha256:[0-9a-f]{64}\Z")


class SpecialistPublicContractError(ValueError):
    """A legacy specialist record cannot cross a public persistence boundary."""

    def __init__(self, field_path: str):
        self.field_path = field_path
        super().__init__("unsafe-legacy-specialist-record")


def _unsafe_provider_identity(value: object) -> bool:
    text = str(value or "")
    return bool(text) and not (
        PORTABLE_PROVIDER_ID.fullmatch(text) or OPAQUE_PROVIDER_ID.fullmatch(text)
    )


def _unsafe_specialist_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(
        value.startswith(("/", "~", "@"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or "/Users/" in value
        or "/private/" in value
        or "/tmp/" in value
    )


def validate_specialist_public_state(data: dict[str, Any]) -> None:
    """Reject unsafe legacy provider fields without copying or reporting values."""
    if not isinstance(data, dict):
        return
    provider_surfaces = (
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
    )
    for surface in provider_surfaces:
        records = data.get(surface) or []
        if not isinstance(records, list):
            raise SpecialistPublicContractError(f"/{surface}")
        for index, record in enumerate(records):
            base = f"/{surface}/{index}"
            if not isinstance(record, dict):
                raise SpecialistPublicContractError(base)
            if "provider_id" in record and _unsafe_provider_identity(record.get("provider_id")):
                raise SpecialistPublicContractError(f"{base}/provider_id")
            for field in ("role", "skill"):
                value = record.get(field)
                if _unsafe_specialist_string(value) or (
                    isinstance(value, str)
                    and any(ord(char) < 32 or ord(char) == 127 for char in value)
                ):
                    raise SpecialistPublicContractError(f"{base}/{field}")
            if record.get("kind") == "command":
                command = record.get("command")
                if command and not PORTABLE_PROVIDER_ID.fullmatch(str(command)):
                    raise SpecialistPublicContractError(f"{base}/command")
                if record.get("args"):
                    raise SpecialistPublicContractError(f"{base}/args")
                if record.get("env"):
                    raise SpecialistPublicContractError(f"{base}/env")
            for field in ("risk", "result_contract", "activation", "auto_use", "detail"):
                if field in record:
                    raise SpecialistPublicContractError(f"{base}/{field}")
    diagnostics = data.get("specialists_ineligible") or []
    if not isinstance(diagnostics, list):
        raise SpecialistPublicContractError("/specialists_ineligible")
    for index, record in enumerate(diagnostics):
        base = f"/specialists_ineligible/{index}"
        if not isinstance(record, dict):
            raise SpecialistPublicContractError(base)
        if "detail" in record:
            raise SpecialistPublicContractError(f"{base}/detail")
        identity = record.get("provider_id")
        if identity != "<registry>" and _unsafe_provider_identity(identity):
            raise SpecialistPublicContractError(f"{base}/provider_id")
    installed = data.get("installed_skills") or []
    if isinstance(installed, list):
        for index, identity in enumerate(installed):
            if _unsafe_provider_identity(identity):
                raise SpecialistPublicContractError(f"/installed_skills/{index}")
    projection = data.get("specialist_registry_projection") or {}
    if isinstance(projection, dict):
        for index, record in enumerate(projection.get("effective_entries") or []):
            if isinstance(record, dict) and _unsafe_provider_identity(record.get("provider_id")):
                raise SpecialistPublicContractError(
                    f"/specialist_registry_projection/effective_entries/{index}/provider_id"
                )
    for index, record in enumerate(data.get("specialist_invocations") or []):
        if not isinstance(record, dict):
            raise SpecialistPublicContractError(f"/specialist_invocations/{index}")
        if "command" in record:
            raise SpecialistPublicContractError(f"/specialist_invocations/{index}/command")
        for field in ("reason", "notes"):
            if _unsafe_specialist_string(record.get(field)):
                raise SpecialistPublicContractError(
                    f"/specialist_invocations/{index}/{field}"
                )
