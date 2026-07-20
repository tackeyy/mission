"""Shared audit finding model and current/historical period classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from mission_common import parse_iso_datetime


FindingPeriod = Literal["current", "historical"]
FINDING_PRIORITIES = ("P0", "P1", "P2")


@dataclass(frozen=True)
class FindingSpec:
    """Stable finding identity and severity, independent from report period."""

    code: str
    priority: str
    source_key: str = ""
    source_kind: str = "item"
    item_summary: str = ""
    aggregate_summary: str = ""


@dataclass(frozen=True)
class AuditFinding:
    """One detected risk with immutable severity and period provenance."""

    spec: FindingSpec
    summary: str
    updated_at: str = ""
    period: FindingPeriod = "current"
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_evidence(
        cls,
        spec: FindingSpec,
        summary: str,
        evidence: dict[str, Any],
        current_since: datetime | None,
    ) -> "AuditFinding":
        updated_at = str(evidence.get("updated_at") or "")
        return cls(
            spec=spec,
            summary=summary,
            updated_at=updated_at,
            period=classify_finding_period(updated_at, current_since),
            provenance=dict(evidence),
        )

    def to_dict(self) -> dict[str, Any]:
        reserved = {"priority", "code", "summary", "period", "updated_at"}
        output = {key: value for key, value in self.provenance.items() if key not in reserved}
        output.update({
            "priority": self.spec.priority,
            "code": self.spec.code,
            "summary": self.summary,
            "period": self.period,
            "updated_at": self.updated_at,
        })
        return output


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_audit_cutoff(value: str | None, *, upper: bool = False) -> datetime | None:
    """Parse date/ISO audit bounds once for since, until, and current-since."""
    if not value:
        return None
    text = value.strip()
    if "T" not in text and len(text) == 10:
        suffix = "T23:59:59.999999+00:00" if upper else "T00:00:00+00:00"
        text = f"{text}{suffix}"
    parsed = parse_iso_datetime(text)
    return _as_utc(parsed) if parsed else None


def classify_finding_period(
    updated_at: str | None,
    current_since: datetime | None,
) -> FindingPeriod:
    """Classify at the inclusive UTC cutoff; missing/invalid evidence stays current."""
    if current_since is None:
        return "current"
    parsed = parse_iso_datetime(updated_at)
    if parsed is None:
        return "current"
    return "current" if _as_utc(parsed) >= _as_utc(current_since) else "historical"


def serialize_findings(findings: Iterable[AuditFinding]) -> list[dict[str, Any]]:
    return [finding.to_dict() for finding in findings]


def finding_counts(findings: Iterable[AuditFinding]) -> dict[str, int]:
    counts = {priority: 0 for priority in FINDING_PRIORITIES}
    total = 0
    for finding in findings:
        if finding.spec.priority in counts:
            counts[finding.spec.priority] += 1
        total += 1
    counts["total"] = total
    return counts


def findings_by_code(findings: Iterable[AuditFinding]) -> dict[str, dict[str, Any]]:
    """Return compact indexes into the canonical period list, not evidence copies."""
    grouped: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(findings):
        bucket = grouped.setdefault(finding.spec.code, {"count": 0, "indexes": []})
        bucket["count"] += 1
        bucket["indexes"].append(index)
    return grouped


def finding_code_counts(findings: Iterable[AuditFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.spec.code] = counts.get(finding.spec.code, 0) + 1
    return counts


def sort_findings(findings: Iterable[AuditFinding]) -> list[AuditFinding]:
    priority_rank = {priority: index for index, priority in enumerate(FINDING_PRIORITIES)}
    return sorted(findings, key=lambda finding: priority_rank.get(finding.spec.priority, len(priority_rank)))


def partition_findings(
    findings: Iterable[AuditFinding],
) -> tuple[list[AuditFinding], list[AuditFinding]]:
    current: list[AuditFinding] = []
    historical: list[AuditFinding] = []
    for finding in findings:
        (current if finding.period == "current" else historical).append(finding)
    return current, historical
