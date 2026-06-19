#!/usr/bin/env python3
"""Audit local /mission state files and emit a self-improvement prompt.

This script is intentionally read-only. It summarizes `.mission-state` sessions
across one or more project roots, deduplicates worktree archives, and prints a
Markdown report that can be handed back to `/mission` for self-improvement.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRUNE_DIRS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}


@dataclass(frozen=True)
class StateRecord:
    path: Path
    state: dict[str, Any]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_date(value: str | None) -> str:
    return (value or "")[:10]


def duration_sec(state: dict[str, Any]) -> float | None:
    started = parse_dt(state.get("started_at"))
    updated = parse_dt(state.get("updated_at"))
    if not started or not updated:
        return None
    seconds = (updated - started).total_seconds()
    return seconds if seconds >= 0 else None


def latest_scored_entry(state: dict[str, Any]) -> dict[str, Any] | None:
    for entry in reversed(state.get("score_history") or []):
        composite = entry.get("composite")
        if isinstance(composite, (int, float)) and not isinstance(composite, bool) and not math.isnan(composite):
            return entry
    return None


def classify(state: dict[str, Any]) -> str:
    if state.get("passes") is True:
        return "pass"
    if state.get("halt_reason"):
        return "halt"
    if not state.get("loop_active"):
        return "abandoned"
    return "incomplete"


def project_name(state: dict[str, Any]) -> str:
    root = str(state.get("project_root") or "").rstrip("/")
    return Path(root).name if root else "unknown"


def project_root_for(record: StateRecord) -> str:
    root = str(record.state.get("project_root") or "").rstrip("/")
    if root:
        return root
    parts = record.path.parts
    if ".mission-state" in parts:
        idx = parts.index(".mission-state")
        if idx > 0:
            return str(Path(*parts[:idx]))
    return str(record.path.parent)


def iter_state_files(root: Path):
    root = root.expanduser()
    if not root.exists():
        return
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if os.path.basename(dirpath) != ".mission-state":
            continue
        dirnames[:] = []
        mission_state = Path(dirpath)
        candidates: list[Path] = []
        sessions = mission_state / "sessions"
        if sessions.is_dir():
            candidates.extend(sorted(sessions.glob("*.json")))
        archive = mission_state / "archive"
        if archive.is_dir():
            candidates.extend(sorted(archive.glob("state-*.json")))
            for worktree_dir in sorted(archive.glob("worktree-*")):
                if worktree_dir.is_dir():
                    candidates.extend(sorted(worktree_dir.glob("*.json")))
        seen: set[Path] = set()
        for path in candidates:
            if path in seen or path.name.endswith(".bak"):
                continue
            seen.add(path)
            yield path


def load_records(roots: list[Path]) -> list[StateRecord]:
    records: list[StateRecord] = []
    for root in roots:
        for path in iter_state_files(root) or []:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            records.append(StateRecord(path=path, state=state))
    return records


def dedupe_records(records: list[StateRecord]) -> tuple[list[StateRecord], list[list[StateRecord]], int]:
    groups: dict[tuple[str, str, str], list[StateRecord]] = {}
    for record in records:
        state = record.state
        key = (
            project_root_for(record),
            str(state.get("session_id") or record.path.stem),
            str(state.get("mission_id") or ""),
            str(state.get("started_at") or ""),
        )
        groups.setdefault(key, []).append(record)

    deduped: list[StateRecord] = []
    duplicates: list[list[StateRecord]] = []
    resolved_duplicate_count = 0
    for group in groups.values():
        if len(group) > 1 and is_resolved_archive_duplicate(group):
            resolved_duplicate_count += 1
        elif len(group) > 1:
            duplicates.append(group)
        deduped.append(sorted(group, key=record_rank)[0])
    return deduped, duplicates, resolved_duplicate_count


def is_resolved_archive_duplicate(group: list[StateRecord]) -> bool:
    """Return true for the expected session + archived worktree copy pattern."""
    if len(group) < 2:
        return False
    ranks = {record_rank(record)[0] for record in group}
    if 0 not in ranks or 1 not in ranks:
        return False
    fingerprints = {json.dumps(record.state, sort_keys=True, ensure_ascii=False) for record in group}
    return len(fingerprints) == 1


def record_rank(record: StateRecord) -> tuple[int, str]:
    path_text = str(record.path)
    if "/sessions/" in path_text:
        rank = 0
    elif "/archive/worktree-" in path_text:
        rank = 1
    else:
        rank = 2
    return (rank, path_text)


def commit_datetime(repo: Path, commit: str) -> datetime:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%cI", commit],
        capture_output=True,
        text=True,
        check=True,
    )
    value = result.stdout.strip()
    parsed = parse_dt(value)
    if not parsed:
        raise ValueError(f"Could not parse commit date for {commit}: {value}")
    return parsed.astimezone(timezone.utc)


def filter_records(
    records: list[StateRecord],
    since: str | None,
    until: str | None,
    after: datetime | None,
) -> list[StateRecord]:
    out: list[StateRecord] = []
    for record in records:
        updated_at = record.state.get("updated_at")
        updated_date = iso_date(updated_at)
        if since and updated_date and updated_date < since:
            continue
        if until and updated_date and updated_date > until:
            continue
        if after:
            updated_dt = parse_dt(updated_at)
            if not updated_dt or updated_dt.astimezone(timezone.utc) <= after:
                continue
        out.append(record)
    return out


def bucket_counts(records: list[StateRecord], bucket_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        bucket = bucket_fn(record)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def halt_or_incomplete_bucket(record: StateRecord) -> str:
    state = record.state
    reason = str(state.get("halt_reason") or "").lower()
    if classify(state) == "incomplete":
        if not state.get("score_history"):
            return "active-no-score-checkpoint"
        return "active-in-progress"
    if any(token in reason for token in ("orphan", "pid", "dead", "stale")):
        return "stale-state-cleanup"
    if "max-iter" in reason or "max iter" in reason:
        return "max-iter"
    if any(token in reason for token in ("user", "confirm", "captcha", "manual", "approval")):
        return "human-blocker"
    if any(token in reason for token in ("api", "auth", "key", "permission", "external")):
        return "external-blocker"
    if not state.get("score_history"):
        return "halted-before-score"
    latest = latest_scored_entry(state)
    if latest and latest.get("composite", 0) < state.get("threshold", 4.0):
        return "below-threshold"
    return "other-halt"


def slow_session_bucket(record: StateRecord, slow_threshold_sec: int) -> str:
    state = record.state
    cls = classify(state)
    duration = duration_sec(state) or 0
    if cls != "pass":
        return f"{cls}-slow"
    if duration >= slow_threshold_sec * 4:
        return "extreme-long-pass"
    if state.get("iteration", 0) >= 2 or state.get("complexity") in {"Complex", "Critical"}:
        return "expected-complex-or-multi-iter"
    latest = latest_scored_entry(state) or {}
    if latest.get("composite", 0) >= 4.3:
        return "healthy-long-pass"
    return "needs-review"


def low_score_pass_bucket(record: StateRecord) -> str:
    state = record.state
    latest = latest_scored_entry(state) or {}
    if latest.get("open_high", 0):
        return "risky-open-high"
    if latest.get("min_item", 0) < 3.5:
        return "risky-min-item-below-gate"
    if latest.get("composite", 0) < state.get("threshold", 4.0):
        return "risky-composite-below-threshold"
    return "valid-threshold-pass"


def aggregate(
    records: list[StateRecord],
    duplicates: list[list[StateRecord]],
    resolved_duplicate_count: int,
    slow_threshold_sec: int,
) -> dict[str, Any]:
    classes = [classify(r.state) for r in records]
    durations = [d for r in records if (d := duration_sec(r.state)) is not None]
    composites = [
        entry["composite"]
        for r in records
        if (entry := latest_scored_entry(r.state)) is not None
    ]
    pass_count = classes.count("pass")
    pass_records = [r for r, cls in zip(records, classes) if cls == "pass"]
    forced = [r for r in records if r.state.get("passes") and r.state.get("passes_forced")]
    ungated = [
        r for r in records
        if r.state.get("passes")
        and latest_scored_entry(r.state) is None
        and not r.state.get("passes_forced")
        and not r.state.get("force_reason")
    ]
    slow = [
        r for r in records
        if (duration_sec(r.state) or 0) >= slow_threshold_sec
    ]
    low_score_pass = [
        r for r in pass_records
        if ((latest_scored_entry(r.state) or {}).get("composite") or 0) < 4.3
    ]
    halt_or_incomplete = [
        r for r, cls in zip(records, classes)
        if cls in {"halt", "incomplete"}
    ]

    by_project: dict[str, dict[str, int]] = {}
    by_agent: dict[str, dict[str, int]] = {}
    for record, cls in zip(records, classes):
        for bucket, key in ((by_project, project_name(record.state)), (by_agent, record.state.get("agent") or "unknown")):
            row = bucket.setdefault(key, {"total": 0, "pass": 0, "halt": 0, "incomplete": 0, "abandoned": 0})
            row["total"] += 1
            row[cls] += 1

    return {
        "total_sessions": len(records),
        "pass_count": pass_count,
        "halt_count": classes.count("halt"),
        "incomplete_count": classes.count("incomplete"),
        "abandoned_count": classes.count("abandoned"),
        "pass_rate": pass_count / len(records) if records else None,
        "forced_pass_count": len(forced),
        "ungated_pass_count": len(ungated),
        "duplicate_group_count": len(duplicates),
        "resolved_duplicate_group_count": resolved_duplicate_count,
        "avg_final_composite": sum(composites) / len(composites) if composites else None,
        "median_session_duration_sec": statistics.median(durations) if durations else None,
        "avg_session_duration_sec": sum(durations) / len(durations) if durations else None,
        "slow_sessions": slow,
        "halt_sessions": [r for r, cls in zip(records, classes) if cls == "halt"],
        "low_score_pass_sessions": low_score_pass,
        "forced_pass_sessions": forced,
        "ungated_pass_sessions": ungated,
        "duplicates": duplicates,
        "halt_incomplete_breakdown": bucket_counts(halt_or_incomplete, halt_or_incomplete_bucket),
        "slow_session_breakdown": bucket_counts(slow, lambda record: slow_session_bucket(record, slow_threshold_sec)),
        "low_score_pass_breakdown": bucket_counts(low_score_pass, low_score_pass_bucket),
        "by_project": by_project,
        "by_agent": by_agent,
    }


def fmt_float(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def fmt_minutes(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds / 60:.1f} min"


def session_line(record: StateRecord) -> str:
    state = record.state
    entry = latest_scored_entry(state) or {}
    duration = duration_sec(state)
    mission = " ".join(str(state.get("mission") or "").split())
    if len(mission) > 90:
        mission = mission[:87] + "..."
    return (
        f"- `{state.get('session_id') or record.path.stem}` "
        f"({project_name(state)}, {state.get('agent') or 'unknown'}, {classify(state)}, "
        f"{fmt_minutes(duration)}, score {fmt_float(entry.get('composite'))}): {mission}"
    )


def finding_rows(stats: dict[str, Any], min_pass_rate: float) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if stats["ungated_pass_count"]:
        rows.append(("P0", "ungated-pass", f"{stats['ungated_pass_count']} pass sessions bypassed scoring gate"))
    if stats["forced_pass_count"]:
        rows.append(("P1", "forced-pass", f"{stats['forced_pass_count']} sessions used force pass"))
    if stats["duplicate_group_count"]:
        rows.append(("P1", "duplicate-state", f"{stats['duplicate_group_count']} duplicate state groups found; stats may double-count"))
    pass_rate = stats["pass_rate"]
    if pass_rate is not None and pass_rate < min_pass_rate:
        rows.append(("P1", "low-pass-rate", f"pass rate {pass_rate * 100:.1f}% is below {min_pass_rate * 100:.0f}%"))
    if stats["halt_count"]:
        rows.append(("P1", "halted-runs", f"{stats['halt_count']} halted sessions need root-cause review"))
    if stats["slow_sessions"]:
        rows.append(("P2", "slow-runs", f"{len(stats['slow_sessions'])} sessions exceeded slow threshold"))
    if stats["low_score_pass_sessions"]:
        rows.append(("P2", "low-score-pass", f"{len(stats['low_score_pass_sessions'])} pass sessions scored below 4.3"))
    if not rows:
        rows.append(("OK", "no-critical-findings", "No forced/ungated pass, halt, duplicate, or slow-session finding"))
    return rows


def self_improvement_prompt(rows: list[tuple[str, str, str]], roots: list[Path], since: str | None, until: str | None) -> str:
    root_args = " ".join(f"--root {root}" for root in roots)
    period = f"--since {since}" if since else ""
    if until:
        period += f" --until {until}"
    finding_text = "\n".join(f"- {p} `{code}`: {summary}" for p, code, summary in rows if p != "OK")
    if not finding_text:
        finding_text = "- 現時点で P0/P1 はなし。P2 以下を ROI 順に確認する。"
    return f"""/mission scripts/mission-audit.py の監査結果をもとに mission 自身を自己改善してください。

監査コマンド:
`python3 scripts/mission-audit.py {root_args} {period} --self-improvement-prompt`

検出事項:
{finding_text}

制約:
- 不可逆操作、外部送信、本番反映は行わない
- まず P0/P1 を最大3件に絞る
- 修正する場合はテストを追加し、既存テストを通す
- `skills/mission/` と `plugins/mission/` の同期が必要な変更は同期テストも通す
- 完了前に `python3 scripts/mission-audit.py {root_args} {period}` を再実行して forced/ungated/duplicate/halt の改善を確認する
"""


def render_markdown(stats: dict[str, Any], rows: list[tuple[str, str, str]], roots: list[Path], since: str | None, until: str | None) -> str:
    lines = [
        "# /mission Audit Report",
        "",
        "## Scope",
        "",
        f"- roots: {', '.join(str(r) for r in roots)}",
        f"- period: {since or '(all)'} ~ {until or '(now)'}",
        "",
        "## Summary",
        "",
        f"- total sessions: {stats['total_sessions']}",
        f"- pass / halt / incomplete / abandoned: {stats['pass_count']} / {stats['halt_count']} / {stats['incomplete_count']} / {stats['abandoned_count']}",
        f"- pass rate: {fmt_float(stats['pass_rate'] * 100 if stats['pass_rate'] is not None else None, 1)}%",
        f"- forced pass: {stats['forced_pass_count']}",
        f"- ungated pass: {stats['ungated_pass_count']}",
        f"- duplicate state groups: {stats['duplicate_group_count']}",
        f"- resolved archive duplicates: {stats['resolved_duplicate_group_count']}",
        f"- avg final composite: {fmt_float(stats['avg_final_composite'])}",
        f"- median duration: {fmt_minutes(stats['median_session_duration_sec'])}",
        f"- avg duration: {fmt_minutes(stats['avg_session_duration_sec'])}",
        "",
        "## Findings",
        "",
    ]
    for priority, code, summary in rows:
        lines.append(f"- **{priority} `{code}`**: {summary}")

    lines.extend(["", "## Halt Sessions", ""])
    if stats["halt_sessions"]:
        lines.extend(session_line(record) for record in stats["halt_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Halt / Incomplete Root-Cause Buckets", ""])
    if stats["halt_incomplete_breakdown"]:
        for key, count in sorted(stats["halt_incomplete_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Slow Sessions", ""])
    if stats["slow_sessions"]:
        lines.extend(session_line(record) for record in stats["slow_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Slow Session Buckets", ""])
    if stats["slow_session_breakdown"]:
        for key, count in sorted(stats["slow_session_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Low-Score Pass Buckets", ""])
    if stats["low_score_pass_breakdown"]:
        for key, count in sorted(stats["low_score_pass_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Duplicate State Groups", ""])
    if stats["duplicates"]:
        for group in stats["duplicates"]:
            lines.append("- " + " | ".join(str(record.path) for record in sorted(group, key=record_rank)))
    else:
        lines.append("- none")

    lines.extend(["", "## By Project", ""])
    for key, row in sorted(stats["by_project"].items()):
        lines.append(f"- `{key}`: total {row['total']}, pass {row['pass']}, halt {row['halt']}, incomplete {row['incomplete']}")

    lines.extend(["", "## Self-Improvement Prompt", "", "```text"])
    lines.append(self_improvement_prompt(rows, roots, since, until).strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit local /mission state logs")
    parser.add_argument("--root", action="append", default=None, help="Root to scan. Can be repeated. Defaults to cwd.")
    parser.add_argument("--since", default=None, help="Updated-at lower bound, YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Updated-at upper bound, YYYY-MM-DD")
    parser.add_argument("--after-commit", default=None, help="Only include sessions updated after this commit date")
    parser.add_argument("--repo", default=".", help="Repository used for --after-commit, default cwd")
    parser.add_argument("--slow-threshold-sec", type=int, default=1800, help="Slow session threshold")
    parser.add_argument("--min-pass-rate", type=float, default=0.9, help="Finding threshold for pass rate")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary")
    parser.add_argument("--out", default=None, help="Write report to path")
    parser.add_argument("--self-improvement-prompt", action="store_true", help="Print only the prompt for /mission")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    roots = [Path(p).expanduser() for p in (args.root or ["."])]
    after = commit_datetime(Path(args.repo).expanduser(), args.after_commit) if args.after_commit else None
    records = load_records(roots)
    filtered = filter_records(records, args.since, args.until, after)
    deduped, duplicates, resolved_duplicate_count = dedupe_records(filtered)
    stats = aggregate(deduped, duplicates, resolved_duplicate_count, args.slow_threshold_sec)
    rows = finding_rows(stats, args.min_pass_rate)

    if args.self_improvement_prompt:
        output = self_improvement_prompt(rows, roots, args.since, args.until)
    elif args.json:
        json_stats = {
            k: v for k, v in stats.items()
            if k not in {
                "duplicates",
                "forced_pass_sessions",
                "halt_sessions",
                "low_score_pass_sessions",
                "slow_sessions",
                "ungated_pass_sessions",
            }
        }
        json_stats["findings"] = [{"priority": p, "code": c, "summary": s} for p, c, s in rows]
        output = json.dumps(json_stats, indent=2, ensure_ascii=False)
    else:
        output = render_markdown(stats, rows, roots, args.since, args.until)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
