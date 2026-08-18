"""Strict per-session repository format selection.

Selection is based only on the loaded session bytes. It never consults an
environment flag and never constructs both writers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Generic, Optional, TypeVar

from mission_kernel.json_codec import decode_json_object, thaw_json_object
from mission_kernel.model import FrozenJsonObject
from mission_kernel.versions import read_schema_version

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


@dataclass(frozen=True)
class RepositoryFormatInspection:
    """Strictly decoded repository format and its embedded session binding."""

    format: RepositoryFormat
    document: FrozenJsonObject
    document_session: Optional[str]


def inspect_repository_bytes(
    source: bytes, *, expected_session_id: Optional[str] = None
) -> RepositoryFormatInspection:
    """Classify one session record without permitting format downgrade."""

    try:
        frozen = decode_json_object(source)
        document = thaw_json_object(frozen)
    except Exception as exc:
        raise RepositorySelectionError("repository-format-invalid") from exc
    schema = document.get("schema")
    if schema == "mission-head/1":
        selected_session = expected_session_id
        if selected_session is None:
            embedded = document.get("session_id")
            selected_session = embedded if isinstance(embedded, str) and embedded else None
        if selected_session is None:
            raise RepositorySelectionError("repository-session-invalid")
        try:
            parse_head(source, selected_session)
        except (FencedCommitError, ValueError) as head_error:
            raise RepositorySelectionError("repository-format-invalid") from head_error
        return RepositoryFormatInspection(
            RepositoryFormat.V5, frozen, selected_session
        )
    if "schema" in document or {"commit", "state_generation"} & set(document):
        raise RepositorySelectionError("repository-format-invalid")
    try:
        read_schema_version(document, max_reader_version=4)
    except Exception as exc:
        raise RepositorySelectionError("repository-format-invalid") from exc
    identity_values = (document.get("mission"), document.get("mission_id"))
    has_identity = any(isinstance(value, str) and value for value in identity_values)
    phase = document.get("phase")
    loop_active = document.get("loop_active")
    has_control = isinstance(phase, str) or type(loop_active) is bool
    if not has_identity or not has_control:
        raise RepositorySelectionError("repository-format-invalid")
    document_session = document.get("session_id")
    if document_session is not None and (
        not isinstance(document_session, str) or not document_session
    ):
        raise RepositorySelectionError("repository-session-invalid")
    if (
        expected_session_id is not None
        and document_session is not None
        and document_session != expected_session_id
    ):
        raise RepositorySelectionError("repository-session-mismatch")
    return RepositoryFormatInspection(
        RepositoryFormat.LEGACY_V4, frozen, document_session
    )


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
        inspected = inspect_repository_bytes(
            source, expected_session_id=self._session_id
        )
        return inspected.format, inspected.document_session

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


def select_repository(
    session_id: str,
    session_path: Path | str,
    legacy_factory: Callable[[Callable[[], object]], RepositoryT],
    v5_factory: Callable[[Callable[[], object]], RepositoryT],
) -> RepositoryT:
    """Construct exactly one retained, format-matching repository."""

    selector: FormatPinnedRepositorySelector[RepositoryT] | None = None

    def guard() -> object:
        if selector is None:
            raise RepositorySelectionError("repository-selector-unbound")
        return selector.select()

    selector = FormatPinnedRepositorySelector(
        session_id=session_id,
        session_path=session_path,
        legacy_factory=lambda: legacy_factory(guard),
        v5_factory=lambda: v5_factory(guard),
    )
    return selector.select().repository
