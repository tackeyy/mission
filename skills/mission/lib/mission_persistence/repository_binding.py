"""Strict per-session repository format selection.

Selection is based only on the loaded session bytes. It never consults an
environment flag and never constructs both writers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Generic, TypeVar

from mission_kernel.json_codec import decode_json_object, thaw_json_object

from .fenced_commit import FencedCommitError, MAX_HEAD_BYTES, _parse_head
from .strict_reader import read_stable_bytes


class RepositorySelectionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RepositoryFormat(str, Enum):
    LEGACY_V4 = "legacy-v4"
    V5 = "v5"


RepositoryT = TypeVar("RepositoryT")


@dataclass(frozen=True)
class RepositorySelection(Generic[RepositoryT]):
    session_id: str
    format: RepositoryFormat
    repository: RepositoryT


class FormatPinnedRepositorySelector(Generic[RepositoryT]):
    """Construct exactly one writer and reject later format drift."""

    def __init__(
        self,
        *,
        session_id: str,
        session_path: Path | str,
        legacy_factory: Callable[[], RepositoryT],
        v5_factory: Callable[[], RepositoryT],
    ) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise RepositorySelectionError("repository-session-invalid")
        if not callable(legacy_factory) or not callable(v5_factory):
            raise RepositorySelectionError("repository-factory-invalid")
        self._session_id = session_id
        self._session_path = Path(session_path)
        self._legacy_factory = legacy_factory
        self._v5_factory = v5_factory
        self._selection: RepositorySelection[RepositoryT] | None = None

    def _loaded_format(self) -> RepositoryFormat:
        try:
            source = read_stable_bytes(self._session_path, limit=4 * 1024 * 1024)
        except Exception as exc:
            raise RepositorySelectionError("repository-session-invalid") from exc
        try:
            document = thaw_json_object(decode_json_object(source))
        except Exception as exc:
            raise RepositorySelectionError("repository-format-invalid") from exc
        if document.get("schema") == "mission-head/1":
            try:
                _parse_head(source, self._session_id)
            except (FencedCommitError, ValueError) as head_error:
                raise RepositorySelectionError("repository-format-invalid") from head_error
            return RepositoryFormat.V5
        if "schema" in document:
            raise RepositorySelectionError("repository-format-invalid")
        schema_version = document.get("schema_version")
        if schema_version is not None and (
            type(schema_version) is not int or schema_version not in {1, 2, 3, 4}
        ):
            # A v5 state generation is immutable content, not a session head;
            # unknown versions cannot silently enter the compatibility writer.
            raise RepositorySelectionError("repository-format-invalid")
        if schema_version is None and not set(document).intersection(
            {"mission", "mission_id", "session_id", "phase", "loop_active"}
        ):
            raise RepositorySelectionError("repository-format-invalid")
        return RepositoryFormat.LEGACY_V4

    def select(self) -> RepositorySelection[RepositoryT]:
        loaded_format = self._loaded_format()
        if self._selection is not None:
            if loaded_format is not self._selection.format:
                raise RepositorySelectionError("repository-format-drift")
            return self._selection
        factory = (
            self._legacy_factory
            if loaded_format is RepositoryFormat.LEGACY_V4
            else self._v5_factory
        )
        repository = factory()
        self._selection = RepositorySelection(
            session_id=self._session_id,
            format=loaded_format,
            repository=repository,
        )
        return self._selection


def require_legacy_session(
    session_id: str, session_path: Path | str
) -> FormatPinnedRepositorySelector[None]:
    """Fail closed before a compatibility adapter can receive a v5 head."""

    selector = FormatPinnedRepositorySelector(
        session_id=session_id,
        session_path=session_path,
        legacy_factory=lambda: None,
        v5_factory=lambda: None,
    )
    selected = selector.select()
    if selected.format is not RepositoryFormat.LEGACY_V4:
        raise RepositorySelectionError("repository-format-v5-requires-uow")
    return selector
