"""Branch-free presentation helpers for CLI adapters."""

from __future__ import annotations


def repository_format_error(error: object, new_mission: bool) -> str:
    suffix = (
        "",
        "; repair or restore the authoritative session repository before "
        "retrying --new-mission",
    )[bool(new_mission)]
    return f"ERROR: repository-format-invalid: {error}{suffix}"
