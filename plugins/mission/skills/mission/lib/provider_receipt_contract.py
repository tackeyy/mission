"""Closed portable receipt and fencing values shared across provider boundaries."""

from __future__ import annotations

from collections.abc import Mapping
import re


_RECEIPT_FIELDS = frozenset({"kind", "identity"})
_OPAQUE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,255}\Z")
_PRIVATE_TOKEN_LOCATOR = re.compile(
    r"(?:file:|(?<![A-Za-z0-9])[A-Za-z]:)", re.IGNORECASE
)


class ProviderReceiptContractError(ValueError):
    """A receipt or fencing value is not safe to persist."""


def validate_closed_provider_receipt(
    value: object, *, required_kind: str | None = None
) -> dict[str, str]:
    """Return one canonical receipt with no local locator or control text."""
    if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
        raise ProviderReceiptContractError("receipt-invalid")
    kind = value.get("kind")
    identity = value.get("identity")
    if (
        kind not in {"process", "provider"}
        or (required_kind is not None and kind != required_kind)
        or not isinstance(identity, str)
        or _OPAQUE_IDENTITY.fullmatch(identity) is None
        or _PRIVATE_TOKEN_LOCATOR.search(identity) is not None
    ):
        raise ProviderReceiptContractError("receipt-invalid")
    return {"kind": kind, "identity": identity}


def validate_fencing_epoch(value: object) -> int:
    """Admit only a positive, non-boolean fencing epoch."""
    if type(value) is not int or value < 1:
        raise ProviderReceiptContractError("fencing-epoch-invalid")
    return value
