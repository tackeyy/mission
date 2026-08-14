"""Error types for the mission kernel codecs."""

from __future__ import annotations


class MissionStateDecodeError(ValueError):
    """Raised when a mission state document fails schema validation."""

    def __init__(self, code: str, json_path: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.json_path = json_path
        self.detail = detail


class StrictReadError(ValueError):
    """Raised when a file cannot be read through the strict persistence boundary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
