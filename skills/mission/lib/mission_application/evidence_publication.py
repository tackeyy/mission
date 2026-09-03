"""#711: evidence publish が UoW へ渡す publication path と blob 識別子.

publication path と blob id は intent / generation / prepare / commit の各
レコードへ永続化される。式が動くと既存レコードを読めなくなるため、導出は
この 1 箇所に置き、呼び出し側で再導出しない。
"""
import hashlib
from pathlib import PurePosixPath

REPOSITORY_ROOT_NAME = ".mission-state"
BLOB_ID_PREFIX = "evidence:"


class EvidencePublicationError(Exception):
    """Reject one publication path that the unit of work cannot carry."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def canonical_publication_path(relative_path: str) -> str:
    """Return the one canonical form both the claim and the binding carry.

    The unit of work resolves projections against the project root and
    refuses its own repository subtree, so the same restriction has to hold
    before the path reaches a binding.
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path must be a non-empty string"
        )
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute():
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path must be relative"
        )
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path has an empty or relative segment"
        )
    if relative_path.count("//"):
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path has an empty segment"
        )
    if parts[0] == REPOSITORY_ROOT_NAME:
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path is inside the repository root"
        )
    return "/".join(parts)


def derive_blob_id(canonical_path: str) -> str:
    """Return the blob identifier bound to one canonical publication path.

    Nothing but the path takes part: a timestamp or an iteration would make
    the identifier differ between the prepare and the retry that follows it.
    """
    if canonical_publication_path(canonical_path) != canonical_path:
        raise EvidencePublicationError(
            "publication-path-invalid", "blob id requires the canonical publication path"
        )
    digest = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
    return BLOB_ID_PREFIX + digest
