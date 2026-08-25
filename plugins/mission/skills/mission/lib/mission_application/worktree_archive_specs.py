"""Application use case for collecting a worktree archive specification."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from typing import Optional


@dataclass(frozen=True)
class WorktreeArchiveSpecsRequest:
    cwd: object
    state_file_path: object
    data: dict
    authoritative: object = None


@dataclass(frozen=True)
class WorktreeArchiveSpecsServices:
    read_authoritative_snapshot: object
    archive_source_file: object
    typed_score_bindings: object
    read_verified_review_input_evidence: object
    read_verified_content_addressed_evidence: object
    normalized_state_reference: object
    resolve_repo_artifact_reference: object
    tracked_repo_artifact_spec: object
    lineage_references: object
    path_name: object
    path_stem: object
    path_suffix: object
    path_from_string: object
    sanitize_session_id: object
    slug_for_filename: object
    failure: object


def _archive_path(services, value: str):
    return services.path_from_string(value)


def _history_path(kind: str, iteration: int, mission8: str, index: int, suffix: str) -> str:
    return f"archive/history/iter-{iteration}-{mission8}-{kind}-{index}{suffix}"


def collect_worktree_archive_specs(request, services) -> list[dict]:
    """Classify, validate, and name the evidence required by one archive."""
    cwd = request.cwd
    state_file_path = request.state_file_path
    data = request.data
    authoritative = request.authoritative
    if authoritative is None:
        authoritative = services.read_authoritative_snapshot(
            state_file_path,
            expected_session_id=services.path_stem(state_file_path),
        )

    session_id = str(data.get("session_id") or "").strip()
    mission_id = str(data.get("mission_id") or "").strip()
    iteration = data.get("iteration")
    if not session_id or not mission_id:
        raise services.failure("session_id and mission_id are required")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
        raise services.failure("iteration must be a non-negative integer")
    if authoritative.loop_active:
        raise services.failure("active session cannot be archived; mark pass or halt first")

    specs: list[dict] = []
    try:
        typed_score_bindings = services.typed_score_bindings(data)
    except ValueError as exc:
        raise services.failure("typed score evidence binding is invalid") from exc

    def typed_score_reference(
        kind: str, item_iteration: int, normalized_reference: str
    ) -> Optional[tuple]:
        matches = [
            binding
            for binding in typed_score_bindings
            if binding.evidence_kind == kind
            and binding.iteration == item_iteration
            and binding.source_reference == normalized_reference
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise services.failure(
                f"content-addressed evidence reference is ambiguous: {kind}"
            )
        return matches[0].reference, matches[0].expected_kind

    def add(
        kind: str,
        reference: str,
        archive_path: str,
        item_iteration: Optional[int] = None,
    ) -> None:
        source, normalized_reference = services.archive_source_file(
            cwd, reference, kind
        )
        effective_iteration = iteration if item_iteration is None else item_iteration
        spec = {
            "evidence_kind": kind,
            "iteration": effective_iteration,
            "source": source,
            "source_reference": normalized_reference,
            "archive_path": _archive_path(services, archive_path),
        }
        if kind == "review-input":
            matches = [
                item
                for item in (data.get("review_evidence_refs") or [])
                if isinstance(item, dict)
                and item.get("path") == normalized_reference
                and item.get("iteration") == effective_iteration
            ]
            if len(matches) != 1:
                raise services.failure(
                    "review input reference is missing or ambiguous"
                )
            try:
                spec["verified_content"] = services.read_verified_review_input_evidence(
                    cwd,
                    matches[0],
                    expected_iteration=effective_iteration,
                )
            except ValueError as exc:
                raise services.failure(
                    "review input evidence integrity mismatch"
                ) from exc
        typed_reference = typed_score_reference(
            kind, effective_iteration, normalized_reference
        )
        if typed_reference is not None:
            reference_document, expected_kind = typed_reference
            try:
                spec["verified_content"] = services.read_verified_content_addressed_evidence(
                    cwd,
                    reference_document,
                    expected_kind=expected_kind,
                )
            except ValueError as exc:
                raise services.failure(
                    f"content-addressed evidence integrity mismatch: {kind}"
                ) from exc
        specs.append(spec)

    state_source, state_reference = services.archive_source_file(
        cwd, str(state_file_path), "state"
    )
    specs.append(
        {
            "evidence_kind": "state",
            "iteration": iteration,
            "source": state_source,
            "source_reference": state_reference,
            "archive_path": _archive_path(
                services, f"sessions/{services.sanitize_session_id(session_id)}.json"
            ),
            "verified_content": authoritative.state_bytes,
        }
    )

    assumptions_path = data.get("assumptions_path")
    if assumptions_path:
        add(
            "assumptions",
            str(assumptions_path),
            f"sessions/{services.path_name(str(assumptions_path))}",
        )

    artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    artifact_path = artifact.get("path")
    if artifact.get("required_for_pass") and not artifact_path:
        raise services.failure("required evidence reference is missing: artifact")
    if artifact_path:
        artifact_reference = str(artifact_path)
        if services.normalized_state_reference(artifact_reference) is not None:
            add(
                "artifact",
                artifact_reference,
                "artifacts/"
                f"{services.sanitize_session_id(session_id)}/"
                f"{services.path_name(artifact_reference)}",
            )
        else:
            try:
                artifact_relative = services.resolve_repo_artifact_reference(
                    cwd, artifact_reference
                )
            except ValueError as exc:
                raise services.failure(str(exc)) from exc
            specs.append(
                services.tracked_repo_artifact_spec(
                    cwd, artifact_relative, "artifact", iteration
                )
            )

    history = [
        entry for entry in (data.get("score_history") or []) if isinstance(entry, dict)
    ]
    if authoritative.passes and not history and not data.get("force_approved_by_user"):
        raise services.failure("required evidence reference is missing: scoring")
    last_by_iteration: dict[int, int] = {}
    for index, entry in enumerate(history):
        entry_iteration = entry.get("iteration")
        if (
            isinstance(entry_iteration, int)
            and not isinstance(entry_iteration, bool)
            and entry_iteration >= 0
        ):
            last_by_iteration[entry_iteration] = index

    mission8 = mission_id[:8]
    for index, entry in enumerate(history):
        entry_iteration = entry.get("iteration")
        if (
            not isinstance(entry_iteration, int)
            or isinstance(entry_iteration, bool)
            or entry_iteration < 0
        ):
            raise services.failure(
                "score_history iteration must be a non-negative integer"
            )
        scoring_reference = str(entry.get("scoring_evidence_path") or "").strip()
        provenance = (
            entry.get("score_provenance")
            if isinstance(entry.get("score_provenance"), dict)
            else {}
        )
        if (
            authoritative.passes
            and not scoring_reference
            and not isinstance(provenance.get("scoring_evidence_ref"), dict)
        ):
            raise services.failure(
                "required evidence reference is missing: "
                f"scoring iteration {entry_iteration}"
            )
        if scoring_reference:
            suffix = services.path_suffix(scoring_reference) or ".json"
            scoring_path = (
                f"archive/iter-{entry_iteration}-{mission8}-scoring{suffix}"
                if last_by_iteration.get(entry_iteration) == index
                else _history_path(
                    "scoring", entry_iteration, mission8, index, suffix
                )
            )
            add("scoring", scoring_reference, scoring_path, entry_iteration)

        reviews_reference = str(entry.get("findings_evidence_path") or "").strip()
        if (
            entry.get("score_source") == "scoring-json"
            and not reviews_reference
            and not isinstance(provenance.get("review_evidence_ref"), dict)
        ):
            raise services.failure(
                "required evidence reference is missing: "
                f"reviews iteration {entry_iteration}"
            )
        if reviews_reference:
            suffix = services.path_suffix(reviews_reference) or ".json"
            reviews_path = (
                f"archive/iter-{entry_iteration}-{mission8}-reviews{suffix}"
                if last_by_iteration.get(entry_iteration) == index
                else _history_path(
                    "reviews", entry_iteration, mission8, index, suffix
                )
            )
            add("reviews", reviews_reference, reviews_path, entry_iteration)

    specialist_counts: dict[tuple, int] = {}
    for invocation in data.get("specialist_invocations") or []:
        if not isinstance(invocation, dict):
            continue
        reference = str(invocation.get("evidence_path") or "").strip()
        if not reference:
            continue
        item_iteration = invocation.get("iteration")
        if (
            not isinstance(item_iteration, int)
            or isinstance(item_iteration, bool)
            or item_iteration < 0
        ):
            item_iteration = iteration
        skill = services.slug_for_filename(
            str(invocation.get("skill") or invocation.get("role") or "unknown")
        )
        key = (item_iteration, skill)
        occurrence = specialist_counts.get(key, 0)
        specialist_counts[key] = occurrence + 1
        suffix = services.path_suffix(reference) or ".md"
        filename = f"iter-{item_iteration}-{mission8}-specialist-{skill}"
        if occurrence:
            filename += f"-{occurrence}"
        add(
            "specialist",
            reference,
            f"archive/{filename}{suffix}",
            item_iteration,
        )

    progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    for field, kind in (
        ("evidence_path", "progress"),
        ("artifact_path", "progress-artifact"),
    ):
        reference = str(progress.get(field) or "").strip()
        if reference:
            suffix = services.path_suffix(reference)
            add(
                kind,
                reference,
                f"archive/iter-{iteration}-{mission8}-{kind}{suffix}",
            )

    expected = services.lineage_references(
        data,
        f".mission-state/sessions/{services.path_name(state_file_path)}",
        repo_root=cwd,
    )
    if expected is None:
        raise services.failure("state lineage references are invalid")
    existing = Counter(
        (
            spec["evidence_kind"],
            spec["iteration"],
            spec["source_reference"],
        )
        for spec in specs
    )
    required = Counter(expected)
    for (kind, item_iteration, reference), count in required.items():
        while existing[(kind, item_iteration, reference)] < count:
            suffix = services.path_suffix(reference) or ".json"
            identity = hashlib.sha256(
                f"{kind}\0{item_iteration}\0{reference}\0"
                f"{existing[(kind, item_iteration, reference)]}".encode("utf-8")
            ).hexdigest()[:16]
            if kind == "artifact" and not reference.startswith(".mission-state/"):
                specs.append(
                    services.tracked_repo_artifact_spec(
                        cwd, reference, kind, item_iteration
                    )
                )
            else:
                add(
                    kind,
                    reference,
                    "archive/lineage/"
                    f"iter-{item_iteration}-{mission8}-{kind}-{identity}{suffix}",
                    item_iteration,
                )
            existing[(kind, item_iteration, reference)] += 1

    destinations: dict[str, object] = {}
    for spec in specs:
        archive_path = (
            spec["archive_path"].as_posix()
            if "archive_path" in spec
            else f"repo-artifact:{spec['source_reference']}"
        )
        if archive_path in destinations:
            raise services.failure(f"duplicate archive path: {archive_path}")
        destinations[archive_path] = spec.get("source")
    return specs
