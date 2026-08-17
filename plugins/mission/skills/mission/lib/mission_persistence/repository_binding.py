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

from .fenced_commit import FencedCommitError, MAX_HEAD_BYTES, parse_head
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
        self._selected_document_session: str | None = None

    def _loaded_format(self) -> tuple[RepositoryFormat, str | None]:
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
                parse_head(source, self._session_id)
            except (FencedCommitError, ValueError) as head_error:
                raise RepositorySelectionError("repository-format-invalid") from head_error
            return RepositoryFormat.V5, self._session_id
        if "schema" in document:
            raise RepositorySelectionError("repository-format-invalid")
        schema_version = document.get("schema_version")
        if schema_version is not None and (
            type(schema_version) is not int or schema_version not in {1, 2, 3, 4}
        ):
            # A v5 state generation is immutable content, not a session head;
            # unknown versions cannot silently enter the compatibility writer.
            raise RepositorySelectionError("repository-format-invalid")
        identity_values = (document.get("mission"), document.get("mission_id"))
        has_identity = any(isinstance(value, str) and value for value in identity_values)
        phase = document.get("phase")
        loop_active = document.get("loop_active")
        has_control = isinstance(phase, str) or type(loop_active) is bool
        if not has_identity or not has_control:
            raise RepositorySelectionError("repository-format-invalid")
        document_session = document.get("session_id")
        if document_session is not None and document_session != self._session_id:
            raise RepositorySelectionError("repository-session-mismatch")
        return RepositoryFormat.LEGACY_V4, document_session

    def select(self) -> RepositorySelection[RepositoryT]:
        loaded_format, document_session = self._loaded_format()
        if self._selection is not None:
            if loaded_format is not self._selection.format:
                raise RepositorySelectionError("repository-format-drift")
            if document_session != self._selected_document_session:
                raise RepositorySelectionError("repository-session-drift")
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
        self._selected_document_session = document_session
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


def select_legacy_repository(
    session_id: str,
    session_path: Path | str,
    legacy_factory: Callable[[Callable[[], object]], RepositoryT],
) -> RepositoryT:
    """Select and construct the actual v4 repository with a retained guard."""

    selector: FormatPinnedRepositorySelector[RepositoryT] | None = None

    def guard() -> object:
        if selector is None:
            raise RepositorySelectionError("repository-selector-unbound")
        return selector.select()

    def build_legacy() -> RepositoryT:
        return legacy_factory(guard)

    def reject_v5() -> RepositoryT:
        raise RepositorySelectionError("repository-format-v5-requires-uow")

    selector = FormatPinnedRepositorySelector(
        session_id=session_id,
        session_path=session_path,
        legacy_factory=build_legacy,
        v5_factory=reject_v5,
    )
    return selector.select().repository
