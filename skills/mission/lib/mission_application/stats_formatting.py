"""Application use case for rendering mission statistics as stable text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StatsTextRequest:
    stats: dict
    since: Optional[str]
    until: Optional[str]
    valid_phases: object


def _pct_detail(rate) -> str:
    """合格に対する比率を " / NN% of PASS" 形式で返す (None なら空文字)."""
    return f" / {rate*100:.0f}% of PASS" if rate is not None else ""


def _ratio_detail(stats: dict, prefix: str) -> str:
    numerator = stats[f"{prefix}_numerator"]
    denominator = stats[f"{prefix}_denominator"]
    rate = stats[prefix]
    percentage = f" ({rate * 100:.1f}%)" if rate is not None else " (-)"
    return f"{numerator}/{denominator}{percentage}"


def _rate_detail(stats: dict, prefix: str) -> str:
    return _ratio_detail(stats, f"{prefix}_pass_rate")


def format_stats_text(request: StatsTextRequest) -> str:
    """Render projected stats without performing I/O or environment access."""
    stats = request.stats
    since = request.since
    until = request.until
    valid_phases = request.valid_phases
    period = f"{since or '(all)'} ~ {until or '(now)'}"
    roots = ", ".join(stats.get("roots") or ["(none)"])
    n = stats["total_sessions"]
    fc = stats["avg_final_composite"]
    sd = stats["avg_session_duration_sec"]
    md = stats.get("median_session_duration_sec")
    lines = [
        f"=== /mission stats ({period}) ===",
        f"roots:                    {roots}",
        f"total_sessions:           {n}",
        f"state_read_errors:        {stats.get('state_read_error_count', 0)}",
        f"duplicate_state_groups:   {stats.get('duplicate_state_group_count', 0)}",
        f"raw_pass_rate:            {_rate_detail(stats, 'raw')}",
        f"completed_pass_rate:      {_rate_detail(stats, 'completed')}",
        f"implementer_pass_rate:    {_rate_detail(stats, 'implementer')}",
        f"evidence_completion_rate: {_ratio_detail(stats, 'evidence_completion_rate')}",
        f"  PASS:                   {stats['pass_count']}",
        f"    (forced:              {stats['forced_pass_count']}{_pct_detail(stats.get('forced_pass_rate'))})",
        f"    (ungated:             {stats['ungated_pass_count']}{_pct_detail(stats.get('ungated_pass_rate'))})",
        f"  active:                 {stats['active_count']}",
        f"  active-no-score:        {stats['active_no_score_count']}",
        f"  stale:                  {stats['stale_count']}",
        f"  HALT:                   {stats['halt_count']}",
    ]
    by_halt_category = stats.get("by_halt_category") or {}
    if by_halt_category:
        lines.append("    (by category)")
        for cat, cnt in by_halt_category.items():
            lines.append(f"      {cat:<18} {cnt}")
    lines.append("terminal_outcomes:")
    for outcome, count in (stats.get("terminal_outcome_counts") or {}).items():
        lines.append(f"  {outcome:<20} {count}")
    lines.append(f"  {'non_terminal':<20} {stats.get('non_terminal_count', 0)}")
    artifact_coverage = stats.get("artifact_coverage") or {}
    artifact_counts = artifact_coverage.get("counts") or {}
    coverage_value = artifact_coverage.get("coverage")
    coverage_text = f"{coverage_value * 100:.1f}%" if coverage_value is not None else "-"
    lines.extend([
        "artifact_coverage:",
        f"  eligible {artifact_counts.get('eligible', 0)} / observed {artifact_counts.get('observed', 0)} / "
        f"missing {artifact_counts.get('missing', 0)} / invalid {artifact_counts.get('invalid', 0)}",
        f"  clean {artifact_counts.get('clean', 0)} / findings {artifact_counts.get('findings', 0)} / "
        f"skipped {artifact_counts.get('skipped', 0)}",
        f"  coverage {coverage_text} / gate_active {str(artifact_coverage.get('gate_active', False)).lower()} / "
        f"counts_conserved {str(artifact_coverage.get('counts_conserved', False)).lower()}",
    ])
    lines += [
        "score_provenance:       verified {verified} / legacy-unverifiable {legacy} / invalid {invalid}".format(
            verified=(stats.get("score_provenance_counts") or {}).get("verified", 0),
            legacy=(stats.get("score_provenance_counts") or {}).get("legacy-unverifiable", 0),
            invalid=(stats.get("score_provenance_counts") or {}).get("invalid", 0),
        ),
        f"  incomplete:             {stats['incomplete_count']}",
        f"  abandoned:              {stats['abandoned_count']}",
        f"avg_iterations:           {stats['avg_iterations']:.2f}" if stats['avg_iterations'] is not None else "avg_iterations: -",
        f"avg_final_composite:      {fc:.2f}" if fc is not None else "avg_final_composite: -",
        f"avg_session_duration:     {sd/60:.1f} min ({sd:.0f}s)" if sd is not None else "avg_session_duration: -",
        f"median_session_duration:  {md/60:.1f} min ({md:.0f}s)" if md is not None else "median_session_duration: -",
    ]
    phase_totals = stats.get("phase_duration_totals_sec") or {}
    if phase_totals:
        lines.append("phase_duration_totals:")
        for phase, sec in sorted(phase_totals.items()):
            # #188: 過去の無検証 set phase= (typo 等) で混入した不正キーを明示する。
            invalid_note = "" if phase in valid_phases else " (invalid: 過去の無検証 set で混入)"
            lines.append(f"  {phase:<14} {sec/60:.1f} min ({sec:.0f}s){invalid_note}")
    activity = stats.get("activity_timing") or {}
    if activity:
        coverage = activity.get("coverage_ratio")
        coverage_text = f"{coverage * 100:.1f}%" if coverage is not None else "-"
        lines.extend([
            "activity_timing:",
            f"  observed:       {activity.get('observed_total_sec', 0.0):.0f}s",
            f"  unclassified:   {activity.get('unclassified_sec', 0.0):.0f}s",
            f"  coverage:       {coverage_text}",
            f"  unobserved gap: {activity.get('unobserved_gap_sec', 0.0):.0f}s",
            f"  totals consistent: {str(activity.get('totals_consistent', False)).lower()}",
            f"  segments:       closed {activity.get('closed_segment_count', 0)} / "
            f"open {activity.get('open_segment_count', 0)} / invalid {activity.get('invalid_segment_count', 0)}",
        ])
        for kind, sec in sorted((activity.get("activity_duration_totals_sec") or {}).items()):
            lines.append(f"  kind {kind:<18} {sec:.0f}s")
        for kind, reasons in sorted((activity.get("wait_reason_totals_sec") or {}).items()):
            for reason, sec in sorted(reasons.items()):
                lines.append(f"  wait {kind}/{reason:<18} {sec:.0f}s")
        for task, values in sorted((activity.get("task_duration_percentiles_sec") or {}).items()):
            lines.append(
                f"  task {task:<16} p50 {values.get('p50')}s / p90 {values.get('p90')}s "
                f"(n={values.get('count', 0)})"
            )
        for phase, values in sorted((activity.get("phase_duration_percentiles_sec") or {}).items()):
            lines.append(
                f"  phase {phase:<14} p50 {values.get('p50')}s / p90 {values.get('p90')}s "
                f"(n={values.get('count', 0)})"
            )
    by_agent = stats.get("by_agent") or {}
    if by_agent:
        lines.append("by_agent:")
        for ag, b in sorted(by_agent.items()):
            lines.append(
                f"  {ag:<14} {b['total']} (PASS {b['pass']} / HALT {b['halt']} / incomplete {b['incomplete']})"
            )
    for label, key in (("by_project", "by_project"), ("by_complexity", "by_complexity"), ("by_review_tier", "by_review_tier"), ("by_cli_version", "by_cli_version")):
        bd = stats.get(key) or {}
        if bd:
            lines.append(f"{label}:")
            for k, b in sorted(bd.items()):
                lines.append(
                    f"  {k:<22} {b['total']} (PASS {b['pass']} / HALT {b['halt']} / incomplete {b['incomplete']} / abandoned {b['abandoned']})"
                )
    hist = stats.get("iteration_histogram") or {}
    if hist:
        lines.append("iteration_histogram:")
        for k in sorted(hist.keys()):
            lines.append(f"  iter {k:<6} {hist[k]}")
    ibrt = stats.get("iteration_by_review_tier") or {}
    if ibrt:
        lines.append("iteration_by_review_tier:")
        for tier in sorted(ibrt.keys()):
            tier_hist = ibrt[tier]
            bucket_str = "  ".join(f"iter {bk}: {bv}" for bk, bv in sorted(tier_hist.items()))
            lines.append(f"  {tier:<14} {bucket_str}")
    return "\n".join(lines)
