"""Filesystem adapter for the disposition evidence observation port."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mission_application.ports import StableEvidenceRead
from mission_persistence.strict_reader import read_stable_bytes_beneath


@dataclass(frozen=True)
class FilesystemEvidenceReader:
    root: Path | str

    def __call__(self, relative_path: str, *, limit: int) -> StableEvidenceRead:
        capture = read_stable_bytes_beneath(self.root, relative_path, limit=limit)
        return StableEvidenceRead(capture.payload, capture.identity)
