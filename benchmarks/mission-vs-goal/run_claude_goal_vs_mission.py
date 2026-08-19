#!/usr/bin/env python3
"""Run a Claude Code /goal command vs /mission benchmark smoke.

This runner uses Claude Code itself for both arms:
- `claude_code_goal_command` sends the official built-in `/goal` command.
- `mission` sends the `/mission` plugin command.

The runner records raw artifacts and JSON output. It is intentionally separate
from the Codex CLI runner so results are not mixed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
MISSION_LIB = REPO_ROOT / "skills" / "mission" / "lib"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))
if str(MISSION_LIB) not in sys.path:
    sys.path.insert(0, str(MISSION_LIB))
from benchmark_audit import summarize_benchmark_kpi
from activity_segments import summarize_activity_states
from artifact_contract import summarize_artifact_coverage
from command_outcomes import observe_state_only
from scoring_provenance import read_score_provenance_evidence

DEFAULT_TASKS_PATH = BENCH_DIR / "tasks.complex.json"
RESULTS_DIR = BENCH_DIR / "results"
ARTIFACTS_DIR = BENCH_DIR / "artifacts"
MISSION_PLUGIN_DIR = REPO_ROOT / "plugins" / "mission"
ARMS = ("claude_code_goal_command", "mission")
MISSION_PROFILES = ("full", "light", "quality")
API_USAGE_LIMIT_MARKERS = (
    "workspace API usage limits",
    "You have reached your specified workspace API usage limits",
)
API_SPEND_LIMIT_MARKERS = (
    "spend limit",
    "rate limit",
    "usage limit",
)
API_SPEND_LIMIT_ERROR_PHRASES = (
    "api error: 429",
    "too many requests",
    "spend limit reached",
    "rate limit reached",
    "usage limit reached",
)
MAX_BUDGET_MARKERS = (
    "max_budget_usd",
    "max budget",
    "maximum budget",
    "budget limit",
)
MAX_BUDGET_ERROR_PHRASES = (
    "max_budget_usd reached",
    "max_budget_usd exceeded",
    "max budget reached",
    "max budget exceeded",
    "maximum budget reached",
    "maximum budget exceeded",
    "budget limit reached",
    "budget limit exceeded",
)


def run_command(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_task_data(tasks_path: Path) -> dict:
    return json.loads(tasks_path.read_text(encoding="utf-8"))


def select_tasks(task_data: dict, limit_tasks: int, task_ids: str | None = None) -> list[dict]:
    tasks = task_data["tasks"]
    if not task_ids:
        return tasks[:limit_tasks]

    requested = [task_id.strip() for task_id in task_ids.split(",") if task_id.strip()]
    by_id = {task["id"]: task for task in tasks}
    missing = [task_id for task_id in requested if task_id not in by_id]
    if missing:
        raise ValueError(f"unknown task id(s): {', '.join(missing)}")
    return [by_id[task_id] for task_id in requested]


def quality_marker_names(task: dict) -> list[str]:
    markers = task.get("quality_markers", [])
    names: list[str] = []
    for marker in markers:
        if isinstance(marker, dict):
            names.append(str(marker["name"]))
        else:
            names.append(str(marker))
    return names


def quality_marker_patterns(marker: str | dict) -> "list[tuple[str, str]]":
    """Return list of (pattern_lowercased, match_type) tuples.

    match_type is taken from the marker dict's ``match_type`` field, defaulting
    to ``"substr"``.  Only ``"substr"`` and ``"regex"`` are valid; any other
    value raises :class:`ValueError`.  For ``"regex"``, the pattern is compiled
    at call time so an invalid regex raises immediately.
    """
    if isinstance(marker, dict):
        raw_values = marker.get("patterns") or [marker["name"]]
        match_type = marker.get("match_type", "substr")
    else:
        raw_values = [marker]
        match_type = "substr"

    if match_type not in ("substr", "regex"):
        raise ValueError(
            f"Invalid match_type {match_type!r}; must be 'substr' or 'regex'."
        )

    result: list[tuple[str, str]] = []
    for value in raw_values:
        pat = str(value).lower()
        if match_type == "regex":
            re.compile(pat, re.DOTALL)  # raises re.error for invalid patterns
        result.append((pat, match_type))
    return result


def strip_form(text: str) -> str:
    """Remove structural markup before marker scoring (F-2, arm-blind).

    Structure must not earn marker credit: an arm that emits more template
    sections would otherwise match section-title markers without content.
    Drops markdown headings, label-only lines (bold labels and bare
    trailing-colon labels), horizontal rules, and table separator rows; keeps
    body prose and table data rows. Kept identical in both runners.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped.startswith("#"):
            continue
        compact = stripped.replace(" ", "")
        # Horizontal rules (***, ___) and table separator rows (|---|:---|, ---).
        if len(compact) >= 3 and len(set(compact)) == 1 and compact[0] in "*_":
            continue
        if set(compact) <= {"|", "-", ":"} and "-" in compact:
            continue
        # Bold label-only lines: **Evidence** / **Stop Decision:**.
        if compact.startswith("**") and compact.endswith("**") and len(compact) > 4:
            continue
        # Bare trailing-colon labels with nothing after the colon.
        if stripped.endswith(":") and "|" not in stripped and len(stripped.rstrip(":").split()) <= 6:
            continue
        kept.append(line)
    return "\n".join(kept)


def _marker_hit(pattern: str, match_type: str, lowered: str) -> bool:
    """Return True if the pattern matches the lowered text."""
    if match_type == "regex":
        return bool(re.search(pattern, lowered, re.DOTALL))
    return pattern in lowered


def evaluate_quality_markers(text: str, task: dict) -> dict:
    markers = task.get("quality_markers", [])
    lowered = text.lower()
    matched: list[str] = []
    missing: list[str] = []
    for marker in markers:
        name = str(marker["name"] if isinstance(marker, dict) else marker)
        patterns = quality_marker_patterns(marker)
        if any(_marker_hit(pat, mt, lowered) for pat, mt in patterns):
            matched.append(name)
        else:
            missing.append(name)

    forbidden_matched: list[str] = []
    for marker in task.get("forbidden_markers", []):
        name = str(marker["name"] if isinstance(marker, dict) else marker)
        patterns = quality_marker_patterns(marker)
        if any(_marker_hit(pat, mt, lowered) for pat, mt in patterns):
            forbidden_matched.append(name)

    total = len(markers)
    recall = len(matched) / total if total else None
    # Net score subtracts false positives (decoys claimed as findings) from
    # planted-marker recall. The penalty is deliberately under-sensitive: a
    # paraphrased false claim can escape the substring match, but nothing an
    # artifact writes can raise the score without matching a planted marker.
    net = max(0.0, (len(matched) - len(forbidden_matched)) / total) if total else None
    return {
        "quality_markers_total": total,
        "quality_markers_matched": matched,
        "quality_markers_missing": missing,
        "quality_marker_recall": round(recall, 2) if recall is not None else None,
        "forbidden_markers_matched": forbidden_matched,
        "quality_marker_score": round(net, 2) if net is not None else None,
    }


def prepare_clone(source: Path, target: Path, starting_commit: str) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = run_command(["git", "clone", "--no-hardlinks", str(source), str(target)], timeout=120)
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr}")
    checkout = run_command(["git", "checkout", "--detach", starting_commit], cwd=target, timeout=120)
    if checkout.returncode != 0:
        raise RuntimeError(f"git checkout failed: {checkout.stderr}")


def sanitize_worktree(worktree: Path, hidden_paths: list[str]) -> list[str]:
    """Remove answer-key files from a cloned worktree before an arm runs.

    Tail-cohort task files embed planted-marker answer keys, so the clone must
    not contain them. Paths are repo-relative; anything resolving outside the
    worktree is rejected. Returns the repo-relative paths actually removed.
    """
    worktree = worktree.resolve()
    removed: list[str] = []
    for rel in hidden_paths:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError(f"hidden path escapes worktree: {rel}")
        target = worktree / rel_path
        # A symlink endpoint is unlinked itself (never followed), so no
        # dangling link is left behind and nothing outside the clone is touched.
        if target.is_symlink():
            target.unlink()
            removed.append(rel)
        elif target.is_dir():
            shutil.rmtree(target)
            removed.append(rel)
        elif target.exists():
            target.unlink()
            removed.append(rel)
    return removed


def build_prompt(
    task: dict,
    arm: str,
    output_rel: str,
    mission_max_iter: int | None = None,
    mission_profile: str = "full",
    mission_budget_minutes: float | None = None,
    extra_rules: list[str] | tuple[str, ...] = (),
) -> str:
    cohort_rules = "".join(f"- {rule}\n" for rule in extra_rules)
    common = f"""You are executing one controlled local benchmark run.

Rules:
- Do not commit, push, install packages, or use network access.
- Write exactly one task artifact at `{output_rel}`.
- Keep edits narrowly scoped to benchmark output files. For the mission arm, `.mission-state/` is also allowed.
- Do not claim benchmark superiority. Only complete this task artifact.
- Include concrete evidence for every claim. If something is unmeasured, say it is unmeasured.
{cohort_rules}
Task id: {task["id"]}
Task category: {task["category"]}
Task prompt: {task["prompt"]}
Task validator: {task["validator"]}
"""
    marker_names = quality_marker_names(task)
    if marker_names and not task.get("markers_hidden"):
        # Tail-cohort tasks set markers_hidden: their markers are planted-defect
        # answer keys, so listing them here would leak the answers to both arms.
        common += "\nQuality scoring markers to cover explicitly when evidence supports them:\n"
        common += "\n".join(f"- {name}" for name in marker_names)
        common += "\n"
    if arm == "claude_code_goal_command":
        return f"""/goal The benchmark artifact exists at `{output_rel}` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

{common}
Arm: claude_code_goal_command

Use Claude Code's official built-in `/goal` command as the completion controller.
The artifact must include these headings:
- Goal
- Result
- Evidence
- Assumptions
- Stop Condition
"""

    mission_complexity = task.get("mission_complexity", "Complex")
    default_mission_iter = 1 if mission_profile == "light" else task.get("mission_max_iter", 2)
    mission_max_iter = mission_max_iter or default_mission_iter
    profile_guidance = ""
    if mission_profile == "light":
        profile_guidance = """
Lightweight benchmark profile:
- Optimize for completing the artifact within a small fixed budget.
- Use a single concise plan/check/write pass; avoid broad repository scans.
- Do not run the full test suite unless the task explicitly requires it.
- Keep `.mission-state/` minimal and use it only if the plugin requires state.
- Stop as soon as the required artifact headings and validator evidence are present.
"""
    elif mission_profile == "quality":
        profile_guidance = """
Quality benchmark profile:
- Optimize for evidence quality, not speed.
- Maintain at least three plausible hypotheses until evidence rejects them.
- Include a quality-marker coverage table and name every missing marker honestly.
- Separate observed evidence, inference, rejected hypotheses, and unmeasured claims.
- Add a reviewer-style stop/proceed decision with residual risks.
- Do not inflate claims: if a marker is unsupported, mark it missing instead of implying coverage.
"""
    # is not None: 0.0 を silent drop せず /mission 側の検証 (正の有限数のみ) に届ける
    budget_flag = (
        f" --budget-minutes {mission_budget_minutes}" if mission_budget_minutes is not None else ""
    )
    fail_first_protocol = ""
    if task.get("fail_first") is True:
        fail_first_protocol = """
Fail-first measurement protocol:
- Iteration 1 is a bounded first pass: leave at least one validator item unverified and name that missing coverage in the artifact.
- Do not run mark-passes in iteration 1; the reviewer must record the missing coverage as a High finding.
- Iteration 2 must resolve that finding before a passing decision and preserve evidence that distinguishes both iterations.
"""
    return f"""/mission Complete the controlled benchmark artifact at `{output_rel}` with auditable mission-style evidence. --max-iter {mission_max_iter}{budget_flag}

{common}
Arm: mission
Mission profile: {mission_profile}

Use the `/mission` plugin workflow. If the mission state CLI routes this task
to the goal contract (a `route: "goal"` verdict or a `routed-goal` halt),
follow the routing: complete the artifact under the goal-contract headings
(Goal / Result / Evidence / Assumptions / Stop Condition), note the routing in
Evidence, and skip the mission-specific headings below. Do not force the
mission loop when the CLI routes. When not routed, run the loop as the
implementer role (the default): maintain auditable mission state, complete at
least one scored review iteration (aggregate-reviews, push-score, mark-passes)
before stopping — this benchmark measures the gated loop, and submitting
evidence without a scored review does not complete it — review the artifact
against the validator, and only report completion when the artifact is
written.
{fail_first_protocol}
{profile_guidance}

The artifact must include these headings:
- Mission
- Plan
- Execution
- Review
- Score
- Stop Decision
- Evidence
- Assumptions

Mission complexity for this task: {mission_complexity}
"""


def parse_claude_json(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"type": "raw", "result": stdout}


def detect_spend_limit(result_json: dict, stderr_text: str, returncode: int | None = None) -> bool:
    """Detect an external API spend/rate/usage limit in a child result.

    Structured error fields are preferred. Text-only fallback requires either
    an error state or a definitive provider error phrase so successful prose
    discussing limit handling is not classified as externally blocked.
    """
    result = result_json if isinstance(result_json, dict) else {}
    if result.get("api_error_status") == 429:
        return True
    result_text = result.get("result", "")
    combined = f"{result_text if isinstance(result_text, str) else ''}\n{stderr_text or ''}".lower()
    has_limit_marker = any(marker in combined for marker in API_SPEND_LIMIT_MARKERS)
    terminal_reason = result.get("terminal_reason")
    has_error_state = (
        result.get("is_error") is True
        or terminal_reason in {"api_error", "error", "rate_limit", "spend_limit", "usage_limit"}
        or (returncode is not None and returncode != 0)
    )
    has_provider_error_phrase = any(phrase in combined for phrase in API_SPEND_LIMIT_ERROR_PHRASES) or (
        has_limit_marker
        and any(phrase in combined for phrase in ("you've hit", "you have hit", "you have reached"))
    )
    return has_limit_marker and (has_error_state or has_provider_error_phrase)


def detect_max_budget_limit(result_json: dict, stderr_text: str, returncode: int | None = None) -> bool:
    """Detect a budget stop without matching successful implementation prose."""
    result = result_json if isinstance(result_json, dict) else {}
    if result.get("subtype") == "error_max_budget_usd":
        return True
    result_text = result.get("result", "")
    combined = f"{result_text if isinstance(result_text, str) else ''}\n{stderr_text or ''}".lower()
    has_budget_marker = any(marker in combined for marker in MAX_BUDGET_MARKERS)
    terminal_reason = result.get("terminal_reason")
    has_error_state = (
        result.get("is_error") is True
        or terminal_reason in {"api_error", "error", "max_budget_usd", "budget_exceeded"}
        or (returncode is not None and returncode != 0)
    )
    provider_lines = {
        " ".join(line.casefold().split())
        for text in (result_text if isinstance(result_text, str) else "", stderr_text or "")
        for line in text.splitlines()
        if line.strip()
    }
    has_provider_error_phrase = any(
        phrase in provider_lines for phrase in MAX_BUDGET_ERROR_PHRASES
    )
    return has_provider_error_phrase or (has_budget_marker and has_error_state)


def classify_run_status(
    stdout: str,
    stderr: str,
    timed_out: bool,
    returncode: int,
    output_exists: bool,
    validator_pass: bool,
) -> dict:
    combined = f"{stdout}\n{stderr}"
    result_json = parse_claude_json(stdout)
    if detect_spend_limit(result_json, stderr, returncode):
        return {
            "run_status": "blocked",
            "blocked_reason": "api_spend_limit",
            "failure_kind": "api_spend_limit",
            "comparable_attempt": False,
        }
    if detect_max_budget_limit(result_json, stderr, returncode):
        return {
            "run_status": "blocked",
            "blocked_reason": "max_budget_usd",
            "failure_kind": "max_budget_usd",
            "comparable_attempt": False,
        }
    if validator_pass:
        return {
            "run_status": "completed",
            "blocked_reason": None,
            "failure_kind": None,
            "comparable_attempt": True,
        }
    if any(marker in combined for marker in API_USAGE_LIMIT_MARKERS):
        return {
            "run_status": "blocked",
            "blocked_reason": "api_usage_limit",
            "failure_kind": "api_usage_limit",
            "comparable_attempt": False,
        }
    if timed_out:
        return {
            "run_status": "blocked",
            "blocked_reason": "timeout",
            "failure_kind": "timeout",
            "comparable_attempt": False,
        }
    if output_exists:
        return {
            "run_status": "failed",
            "blocked_reason": None,
            "failure_kind": "validator",
            "comparable_attempt": True,
        }
    if returncode != 0:
        return {
            "run_status": "failed",
            "blocked_reason": None,
            "failure_kind": "command_error",
            "comparable_attempt": True,
        }
    return {
        "run_status": "failed",
        "blocked_reason": None,
        "failure_kind": "missing_artifact",
        "comparable_attempt": True,
    }


def score_from_signals(
    validator_pass: bool,
    marker_score: float | None,
    validator_fraction: float | None = None,
    has_markers: bool = True,
) -> tuple[float, float]:
    """Deterministic, arm-blind scoring (F-1; gradient v2 per #247).

    The arm identity is never consulted here; the same artifact signals produce
    the same score for any arm. Returns (quality_score, evidence_completeness).

    Markered tasks (#247 gradient v2):
        quality = 1.0 + 1.0 * validator_fraction + 3.0 * (marker_score or 0.0)
    Content recall dominates, so validator-pass + full-marker no longer pins every
    completed record to the 5.0 ceiling by structure alone; partial marker recall
    produces a graded score.

    Marker-less tasks keep the legacy binary mapping (1.0 fail / 4.0 pass) so the
    historical meaning of the baseline cohorts is unchanged.
    """
    if not has_markers:
        value = round(1.0 + 3.0 * (1.0 if validator_pass else 0.0), 2)
        return value, value
    fraction = validator_fraction if validator_fraction is not None else (1.0 if validator_pass else 0.0)
    base = marker_score if marker_score is not None else 0.0
    value = round(1.0 + 1.0 * fraction + 3.0 * base, 2)
    return value, value


def counterbalanced_plan(tasks: list[dict], arms) -> list[tuple[dict, str, int]]:
    """Alternate which arm runs first by task index to cancel run-order bias (F-1).

    Even task index -> arms in declared order; odd index -> reversed. Deterministic
    (no randomness, for reproducibility). Returns (task, arm, arm_order) triples,
    where arm_order is 1 for the arm that runs first for that task.
    """
    ordered_arms = tuple(arms)
    plan: list[tuple[dict, str, int]] = []
    for idx, task in enumerate(tasks):
        sequence = ordered_arms if idx % 2 == 0 else tuple(reversed(ordered_arms))
        for order, arm in enumerate(sequence, start=1):
            plan.append((task, arm, order))
    return plan


def expanded_plan(tasks: list[dict], arms, repeats: int) -> list[tuple[dict, str, int, int]]:
    """#249: counterbalanced plan を repeats 回展開し run_index (1..N) を付与する。

    counterbalance は反復内で維持される。repeats=1 は legacy plan と同一順序。
    """
    plan: list[tuple[dict, str, int, int]] = []
    for run_index in range(1, max(1, repeats) + 1):
        for task, arm, arm_order in counterbalanced_plan(tasks, arms):
            plan.append((task, arm, arm_order, run_index))
    return plan


def run_name_for(task_id: str, arm: str, run_index: int, repeats: int) -> str:
    """#249: 反復時のみ -repN suffix を付け、artifacts/run-output の衝突を防ぐ。

    repeats=1 は legacy 命名を維持し、既存 run のパス互換を保つ。
    """
    if repeats > 1:
        return f"{task_id}-{arm}-rep{run_index}"
    return f"{task_id}-{arm}"


def extract_process_quality(worktree: Path) -> tuple[dict | None, str | None]:
    """#560 Step A: archive の reviews/scoring JSON から process quality を fail-open で収集する。

    ファイルが無い・壊れている場合は (None, error_reason) を返す。
    Severity が不明な値は literal 文字列キーとして by_severity に追加する（クラッシュしない）。
    """
    archive = worktree / ".mission-state" / "archive"
    if not archive.is_dir():
        return None, "archive_dir_missing"

    iter_pattern = re.compile(r"^iter-(\d+)-")

    def parse_iter(name: str) -> int | None:
        m = iter_pattern.match(name)
        return int(m.group(1)) if m else None

    # Gather reviews files grouped by iteration number
    reviews_by_iter: dict[int, list[Path]] = {}
    scoring_by_iter: dict[int, list[Path]] = {}
    try:
        for child in archive.iterdir():
            if not child.is_file() or not child.suffix == ".json":
                continue
            n = parse_iter(child.name)
            if n is None:
                continue
            if "-reviews-" in child.name:
                reviews_by_iter.setdefault(n, []).append(child)
            elif "-scoring-" in child.name:
                scoring_by_iter.setdefault(n, []).append(child)
    except OSError as exc:
        return None, f"archive_list_error:{type(exc).__name__}"

    if not reviews_by_iter:
        return None, "archive_reviews_missing"

    sorted_iters = sorted(reviews_by_iter)
    findings_total = 0
    # High/Medium/Low は常に存在させる (消費側の KeyError を防ぐ)。
    # 未知の severity は literal キーとして追加される。
    by_severity: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0}
    reviewer_count_observed = 0

    for n in sorted_iters:
        for path in reviews_by_iter[n]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                return None, f"archive_reviews_unreadable:{type(exc).__name__}"
            if not isinstance(payload, dict):
                return None, "archive_reviews_not_object"
            inputs = payload.get("inputs")
            if not isinstance(inputs, list):
                continue
            reviewer_count_observed = max(reviewer_count_observed, len(inputs))
            for inp in inputs:
                if not isinstance(inp, dict):
                    continue
                findings = inp.get("findings")
                if not isinstance(findings, list):
                    continue
                for finding in findings:
                    if not isinstance(finding, dict):
                        continue
                    findings_total += 1
                    sev = finding.get("severity")
                    key = sev if isinstance(sev, str) else "__unknown__"
                    by_severity[key] = by_severity.get(key, 0) + 1

    def _read_composite(n: int) -> tuple[float | None, int | None]:
        paths = scoring_by_iter.get(n, [])
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            for key in ("composite", "binding", "_meta"):
                val = payload.get(key)
                if key == "binding" and isinstance(val, dict):
                    val = val.get("composite")
                elif key == "_meta" and isinstance(val, dict):
                    val = val.get("computed_composite")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    open_high = payload.get("open_high")
                    oh = open_high if isinstance(open_high, int) and not isinstance(open_high, bool) else None
                    return float(val), oh
        return None, None

    first_iter = sorted_iters[0]
    last_iter = sorted_iters[-1]
    composite_first, open_high_first = _read_composite(first_iter)
    composite_final, open_high_final = _read_composite(last_iter)

    return {
        "review_findings_total": findings_total,
        "review_findings_by_severity": by_severity,
        "review_iterations_observed": len(sorted_iters),
        "composite_first": composite_first,
        "composite_final": composite_final,
        "reviewer_count_observed": reviewer_count_observed,
        "open_high_first": open_high_first,
        "open_high_final": open_high_final,
    }, None


def extract_mission_state_fields(worktree: Path) -> tuple[dict, str | None]:
    """#250: mission arm 実行後の state から tier/iteration 等を fail-open で抽出する。

    state が無い・壊れている場合は全フィールド None + note を返し、run 自体は
    失敗させない (計装は record の成立を妨げない)。
    """
    fields = {
        "mission_review_tier": None,
        "mission_iterations": None,
        "mission_complexity": None,
        "mission_passes": None,
        "mission_halt_category": None,
        "mission_routed": None,  # #333: routed-goal halt = true / state あり非 routed = false
        "mission_evidence_only": None,  # #341: evidence-submitted halt = true (gated loop 未実行)
        "measurement_observations": unavailable_measurement_observations(),
        "diff_review_observations": None,
        "process_quality": None,
        "process_quality_error": None,
    }
    # process_quality は session state の可読性と独立に収集する (#560)。
    # session が読めない早期 return 経路でも archive は存在しうるため、先に収集する。
    try:
        pq, pq_err = extract_process_quality(worktree)
        fields["process_quality"] = pq
        fields["process_quality_error"] = pq_err
    except Exception as exc:  # noqa: BLE001 — never abort the run
        fields["process_quality"] = None
        fields["process_quality_error"] = f"process_quality_exception:{type(exc).__name__}"
    sessions = worktree / ".mission-state" / "sessions"
    candidates = sorted(sessions.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if sessions.is_dir() else []
    legacy = worktree / ".mission-state" / "state.json"
    if not candidates and legacy.exists():
        candidates = [legacy]
    if not candidates:
        return fields, "mission_state_missing"
    try:
        state = json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return fields, f"mission_state_unreadable:{type(exc).__name__}"
    if not isinstance(state, dict):
        return fields, "mission_state_not_object"
    tier = state.get("review_tier")
    fields["mission_review_tier"] = tier if isinstance(tier, str) else None
    iteration = state.get("iteration")
    fields["mission_iterations"] = iteration if isinstance(iteration, int) and not isinstance(iteration, bool) else None
    complexity = state.get("complexity")
    fields["mission_complexity"] = complexity if isinstance(complexity, str) else None
    passes = state.get("passes")
    fields["mission_passes"] = passes if isinstance(passes, bool) else None
    halt_category = state.get("halt_category")
    fields["mission_halt_category"] = halt_category if isinstance(halt_category, str) else None
    fields["mission_routed"] = halt_category == "routed-goal"  # #333
    fields["mission_evidence_only"] = halt_category == "evidence-submitted"  # #341
    fields["measurement_observations"] = extract_measurement_observations(state)
    fields["diff_review_observations"] = extract_diff_review_observations(worktree, state)
    return fields, None


def unavailable_diff_review_observations() -> dict:
    return {"schema": "mission-diff-review-observations/1", "status": "unavailable", "critic_has_new_scope": None, "iterations": []}


def _positive_iteration(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _score_timestamp(value: object) -> tuple[datetime | None, bool]:
    if not isinstance(value, str):
        return None, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, True
    return parsed.astimezone(timezone.utc), True


def _safe_aggregate_context(worktree: Path, reference: object, iteration: int) -> dict:
    unavailable = {"context_mode_expected": None, "context_manifest_generated": None, "context_manifest_fallback": None}
    if not isinstance(reference, dict) or reference.get("kind") != "review-aggregate":
        return unavailable
    rel_path, digest = reference.get("path"), reference.get("digest")
    if not isinstance(rel_path, str) or not isinstance(digest, str) or not digest.startswith("sha256:"):
        return unavailable
    try:
        content = read_score_provenance_evidence(worktree, rel_path)
        if hashlib.sha256(content).hexdigest() != digest.removeprefix("sha256:"):
            return unavailable
        payload = json.loads(content.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return unavailable
    if not isinstance(payload, dict) or payload.get("schema") != "mission-review-aggregate/1" or payload.get("iteration") != iteration:
        return unavailable
    expected, generated = payload.get("context_mode_expected"), payload.get("context_manifest_generated")
    if expected not in {"bounded", "full"} or not isinstance(generated, bool):
        return unavailable
    return {
        "context_mode_expected": expected,
        "context_manifest_generated": generated,
        "context_manifest_fallback": expected == "bounded" and not generated,
    }


def extract_diff_review_observations(worktree: Path, state: dict) -> dict:
    """Extract per-iteration measurement only from state and verified evidence."""
    rows: dict[int, dict] = {}

    def row_for(iteration: int) -> dict:
        return rows.setdefault(iteration, {
            "iteration": iteration,
            "activity_duration_sec": {},
            "wall_clock_sec": None,
            "wall_clock_status": "unavailable",
            "record_total_cost_usd": None,
            "per_iteration_cost_status": "unavailable",
            "context_mode_expected": None,
            "context_manifest_generated": None,
            "context_manifest_fallback": None,
        })
    for segment in state.get("activity_segments") or []:
        if not isinstance(segment, dict):
            continue
        iteration = _positive_iteration(segment.get("iteration"))
        seconds = segment.get("duration_sec")
        kind, phase = segment.get("kind"), segment.get("phase")
        if iteration is None or isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0 or not isinstance(kind, str) or not isinstance(phase, str):
            continue
        row = row_for(iteration)
        by_kind = row["activity_duration_sec"].setdefault(kind, {})
        by_kind[phase] = round(by_kind.get(phase, 0.0) + float(seconds), 6)

    history = state.get("score_history")
    scored: list[tuple[int, datetime, dict]] = []
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, dict):
                continue
            iteration = _positive_iteration(entry.get("iteration"))
            when, timestamp_valid = _score_timestamp(entry.get("timestamp"))
            if iteration is None:
                continue
            if not timestamp_valid:
                continue
            row = row_for(iteration)
            if when is None:
                continue
            reference = entry.get("review_evidence_ref")
            if not isinstance(reference, dict):
                provenance = entry.get("score_provenance")
                reference = provenance.get("review_evidence_ref") if isinstance(provenance, dict) else None
            row.update(_safe_aggregate_context(worktree, reference, iteration))
            scored.append((iteration, when, row))
    previous: datetime | None = None
    for _iteration, when, row in sorted(scored):
        if previous is not None and when >= previous:
            row["wall_clock_sec"] = round((when - previous).total_seconds(), 6)
            row["wall_clock_status"] = "observed"
        else:
            row["wall_clock_sec"] = None
            row["wall_clock_status"] = "unavailable"
        previous = when
    if not rows:
        return unavailable_diff_review_observations()
    critic_has_new_scope = state.get("critic_has_new_scope")
    return {
        "schema": "mission-diff-review-observations/1", "status": "observed",
        "critic_has_new_scope": critic_has_new_scope if isinstance(critic_has_new_scope, bool) else None,
        "iterations": [rows[key] for key in sorted(rows)],
    }


def attach_diff_review_record_cost(observations: object, total_cost_usd: object) -> None:
    """Attach the record-level cost without inventing a per-iteration allocation."""
    if not isinstance(observations, dict) or not isinstance(observations.get("iterations"), list):
        return
    value = (
        float(total_cost_usd)
        if isinstance(total_cost_usd, (int, float))
        and not isinstance(total_cost_usd, bool)
        and math.isfinite(total_cost_usd)
        and total_cost_usd >= 0
        else None
    )
    for row in observations["iterations"]:
        if isinstance(row, dict) and _positive_iteration(row.get("iteration")) is not None:
            row["record_total_cost_usd"] = value
            row["per_iteration_cost_status"] = "unavailable"


def _has_positive_activity_duration(row: object) -> bool:
    durations = row.get("activity_duration_sec") if isinstance(row, dict) else None
    if not isinstance(durations, dict):
        return False
    return any(
        isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and math.isfinite(seconds)
        and seconds > 0
        for phases in durations.values() if isinstance(phases, dict)
        for seconds in phases.values()
    )


MEASUREMENT_RATE_FIELDS = (
    "artifact_observation_coverage",
    "activity_coverage",
    "structured_score_provenance",
    "reviewer_freshness",
    "force_pass_rate",
)
MEASUREMENT_COUNTER_FIELDS = ("expected_gate_retry_count",)
MEASUREMENT_UNAVAILABLE_FIELDS = ("group_closeout_completeness",)


def unavailable_measurement_observations(*, status: str = "unavailable") -> dict:
    return {
        key: {"status": status, "value": None}
        for key in (*MEASUREMENT_RATE_FIELDS, *MEASUREMENT_COUNTER_FIELDS, *MEASUREMENT_UNAVAILABLE_FIELDS)
    }


def _rate_observation(numerator: int | float, denominator: int | float) -> dict:
    if denominator <= 0:
        return {"status": "unavailable", "value": None}
    return {"status": "observed", "numerator": numerator, "denominator": denominator}


def extract_measurement_observations(state: dict) -> dict:
    """Reduce typed mission-state observations without reading planning-provider state."""
    observations = unavailable_measurement_observations()

    artifact = summarize_artifact_coverage([state])
    artifact_counts = artifact["counts"]
    observations["artifact_observation_coverage"] = _rate_observation(
        artifact_counts["observed"], artifact_counts["eligible"],
    )

    activity = summarize_activity_states([state])
    activity_numerator = activity.get("observed_total_sec")
    activity_denominator = activity.get("coverage_denominator_sec")
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in (activity_numerator, activity_denominator)
    ):
        observations["activity_coverage"] = _rate_observation(
            activity_numerator, activity_denominator,
        )

    history = state.get("score_history")
    if isinstance(history, list):
        entries = [entry for entry in history if isinstance(entry, dict)]
        if entries:
            observations["structured_score_provenance"] = _rate_observation(
                sum(isinstance(entry.get("score_provenance"), dict) for entry in entries),
                len(entries),
            )

    iteration = state.get("iteration")
    references = state.get("review_evidence_refs")
    if isinstance(references, list):
        perspectives = {
            reference.get("perspective")
            for reference in references
            if isinstance(reference, dict)
            and reference.get("iteration") == iteration
            and isinstance(reference.get("perspective"), str)
            and reference["perspective"]
        }
        reviewer_count = state.get("reviewer_count")
        if perspectives and isinstance(reviewer_count, int) and not isinstance(reviewer_count, bool) and reviewer_count > 0:
            observations["reviewer_freshness"] = _rate_observation(len(perspectives), reviewer_count)

    if isinstance(state.get("passes"), bool):
        observations["force_pass_rate"] = _rate_observation(
            int(state.get("passes_forced") is True), 1,
        )

    try:
        outcomes = observe_state_only(state)["records"]
    except Exception:
        outcomes = None
    if isinstance(outcomes, list):
        observations["expected_gate_retry_count"] = {
            "status": "observed",
            "count": sum(
                1 for outcome in outcomes
                if outcome.get("outcome_kind") == "expected-gate"
                and (outcome.get("attempt", 1) > 1 or outcome.get("retry_of") is not None)
            ),
        }
    return observations


def evaluate_run(
    worktree: Path,
    task: dict,
    arm: str,
    output_rel: str,
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> dict:
    output_path = worktree / output_rel
    text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    # #248 (B4): validator gate は両アーム共通の見出しのみ。goal 5 見出し vs
    # mission 8 見出しの非対称は完走判定の難易度差と「冗長に書くほど有利」の
    # 歪みを生むため、アーム固有見出しは情報として記録するが gate しない。
    common_headings = ["Evidence", "Assumptions"]
    if arm == "claude_code_goal_command":
        arm_headings = ["Goal", "Result", "Stop Condition"]
    else:
        arm_headings = ["Mission", "Plan", "Execution", "Review", "Score", "Stop Decision"]

    missing_headings = [heading for heading in common_headings if heading not in text]
    missing_arm_specific = [heading for heading in arm_headings if heading not in text]
    completion = returncode == 0 and output_path.exists()
    validator_pass = completion and not missing_headings
    # #247 (B1): 共通見出しの充足率 (0..1)。arm 固有を混ぜると非対称が再燃するため
    # 共通見出しのみで計算する。
    validator_fraction = (
        (len(common_headings) - len(missing_headings)) / len(common_headings)
        if completion
        else 0.0
    )
    # F-2: markers are scored against the form-stripped body, so template
    # structure earns no marker credit. The unstripped score is kept as
    # quality_marker_score_raw for comparability with pre-F-2 records.
    marker_eval = evaluate_quality_markers(strip_form(text), task)
    marker_eval_raw = evaluate_quality_markers(text, task)
    if not validator_pass:
        marker_eval["quality_marker_score"] = None
        marker_eval["quality_marker_recall"] = None
        marker_eval_raw["quality_marker_score"] = None
    marker_ratio = marker_eval["quality_marker_score"]
    has_markers = bool(task.get("quality_markers"))
    quality_score, evidence_score = score_from_signals(
        validator_pass,
        marker_ratio,
        validator_fraction=validator_fraction,
        has_markers=has_markers,
    )
    status = classify_run_status(
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        returncode=returncode,
        output_exists=output_path.exists(),
        validator_pass=validator_pass,
    )

    return {
        "completion": completion,
        "validator_pass": validator_pass,
        **status,
        "human_quality_score": quality_score,
        # #247: markered task は gradient v2、marker-less は legacy 二値の意味を保つ。
        # method 文字列で新旧 record を機械的に区別できる。
        "quality_score_method": (
            "automated_heuristic_form_stripped_gradient_v2_not_blind_human_regex_v3"
            if has_markers
            else "automated_heuristic_form_stripped_not_blind_human"
        ),
        "intervention_count": 0,
        "resume_success": None if "resume" not in task["id"] else validator_pass,
        "evidence_completeness": evidence_score,
        "missing_headings": missing_headings,
        "missing_arm_specific_headings": missing_arm_specific,
        "validator_fraction": round(validator_fraction, 2),
        "artifact_bytes": output_path.stat().st_size if output_path.exists() else 0,
        **marker_eval,
        "quality_marker_score_raw": marker_eval_raw["quality_marker_score"],
    }


def copy_artifacts(worktree: Path, artifact_dir: Path, output_rel: str, stdout_path: Path, stderr_path: Path) -> list[str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source, name in (
        (worktree / output_rel, "artifact.md"),
        (stdout_path, "claude-result.json"),
        (stderr_path, "stderr.txt"),
    ):
        if source.exists():
            dest = artifact_dir / name
            if source.resolve() != dest.resolve():
                shutil.copy2(source, dest)
            copied.append(str(dest.relative_to(REPO_ROOT)))
    diff = run_command(["git", "diff", "--", output_rel, ".mission-state"], cwd=worktree, timeout=60)
    diff_path = artifact_dir / "diff.patch"
    diff_path.write_text(diff.stdout, encoding="utf-8")
    copied.append(str(diff_path.relative_to(REPO_ROOT)))
    return copied


def as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_one(
    task: dict,
    arm: str,
    run_id: str,
    starting_commit: str,
    run_root: Path,
    timeout: int,
    max_budget_usd: float | None,
    mission_max_iter: int | None,
    mission_profile: str,
    arm_order: int,
    model_id: str,
    mission_budget_minutes: float | None = None,
    hidden_paths: list[str] | None = None,
    extra_rules: list[str] | tuple[str, ...] = (),
    run_index: int = 1,
    repeats: int = 1,
    max_budget_usd_goal: float | None = None,
    max_budget_usd_mission: float | None = None,
) -> dict:
    task_id = task["id"]
    run_name = run_name_for(task_id, arm, run_index, repeats)
    worktree = run_root / run_name / "repo"
    prepare_clone(REPO_ROOT, worktree, starting_commit)
    sanitized = sanitize_worktree(worktree, hidden_paths or [])
    output_rel = f"benchmarks/mission-vs-goal/run-output/{run_id}/{run_name}.md"
    prompt = build_prompt(
        task,
        arm,
        output_rel,
        mission_max_iter=mission_max_iter,
        mission_profile=mission_profile,
        mission_budget_minutes=mission_budget_minutes,
        extra_rules=extra_rules,
    )
    artifact_dir = ARTIFACTS_DIR / run_id / run_name
    stdout_path = artifact_dir / "claude-result.json"
    stderr_path = artifact_dir / "stderr.txt"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    effective_max_budget_usd = resolve_max_budget_usd(
        arm,
        max_budget_usd=max_budget_usd,
        max_budget_usd_goal=max_budget_usd_goal,
        max_budget_usd_mission=max_budget_usd_mission,
    )
    command = build_child_command(
        arm,
        prompt,
        max_budget_usd=max_budget_usd,
        max_budget_usd_goal=max_budget_usd_goal,
        max_budget_usd_mission=max_budget_usd_mission,
    )

    started = iso_now()
    start_time = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            cwd=worktree,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=child_env(dict(os.environ)),  # #268: permission-mode 降格防止
        )
        stdout = proc.stdout
        stderr = proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = as_text(exc.stdout)
        stderr = as_text(exc.stderr) + f"\nTIMEOUT after {timeout} seconds\n"
        returncode = 124
    elapsed = round((time.monotonic() - start_time) / 60, 2)
    completed = iso_now()
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    claude_result = parse_claude_json(stdout)
    evaluation = evaluate_run(worktree, task, arm, output_rel, returncode, stdout, stderr, timed_out)
    # #250: mission arm の tier/iteration 帰属 (fail-open)。goal arm は全 None。
    if arm == "mission":
        mission_state_fields, mission_state_note = extract_mission_state_fields(worktree)
    else:
        mission_state_fields, mission_state_note = (
            {
                "mission_review_tier": None,
                "mission_iterations": None,
                "mission_complexity": None,
                "mission_passes": None,
                "mission_halt_category": None,
                "mission_routed": None,
                "mission_evidence_only": None,
                "measurement_observations": unavailable_measurement_observations(status="not-applicable"),
                "diff_review_observations": unavailable_diff_review_observations(),
                "process_quality": None,
                "process_quality_error": None,
            },
            None,
        )
    # #261: mission ループ未初期化の record を無効化 (aggregate 希釈防止)
    guarded = apply_mission_adherence_guard(
        {k: evaluation[k] for k in ("run_status", "blocked_reason", "failure_kind", "comparable_attempt")},
        arm=arm,
        mission_state_note=mission_state_note,
        task_complexity=task.get("mission_complexity"),
    )
    evaluation.update(guarded)
    # #333: init 経路 routing (Simple + state 不在 + 完走) を routed として記録
    if (
        arm == "mission"
        and mission_state_note == "mission_state_missing"
        and task.get("mission_complexity") == "Simple"
        and evaluation.get("run_status") == "completed"
    ):
        mission_state_fields["mission_routed"] = True
    artifacts = copy_artifacts(worktree, artifact_dir, output_rel, stdout_path, stderr_path)

    usage = claude_result.get("usage", {}) if isinstance(claude_result, dict) else {}
    notes = [
        f"claude_returncode={returncode}",
        f"claude_session_id={claude_result.get('session_id') if isinstance(claude_result, dict) else None}",
        f"claude_total_cost_usd={claude_result.get('total_cost_usd') if isinstance(claude_result, dict) else None}",
        f"quality_score_method={evaluation['quality_score_method']}",
        f"mission_profile={mission_profile if arm == 'mission' else None}",
        "print_mode_smoke=true",
    ]
    if sanitized:
        notes.append(f"sanitized_hidden_paths={','.join(sanitized)}")
    if mission_state_note:
        notes.append(f"mission_state_note={mission_state_note}")
    if timed_out:
        notes.append(f"timed_out_after_seconds={timeout}")
    if evaluation["run_status"] == "blocked":
        notes.append(f"blocked_reason={evaluation['blocked_reason']}")
    if evaluation["missing_headings"]:
        notes.append(f"missing_headings={','.join(evaluation['missing_headings'])}")
    if evaluation["quality_markers_total"]:
        notes.append(f"quality_marker_score={evaluation['quality_marker_score']}")
        notes.append(f"quality_markers_matched={','.join(evaluation['quality_markers_matched'])}")
        notes.append(f"quality_markers_missing={','.join(evaluation['quality_markers_missing'])}")
    if stderr.strip():
        notes.append("stderr captured in artifact")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_cost_usd = (
        claude_result.get("total_cost_usd") if isinstance(claude_result, dict) else None
    )
    attach_diff_review_record_cost(
        mission_state_fields.get("diff_review_observations"), total_cost_usd,
    )
    burn_rate_usd_per_min = calculate_burn_rate_usd_per_min(
        evaluation["run_status"],
        total_cost_usd,
        elapsed,
    )

    return {
        "benchmark": "mission-vs-goal-pilot",
        "run_id": run_id,
        "task_id": task_id,
        "arm": arm,
        "arm_order": arm_order,
        "model": "claude_code_default",
        "model_id": model_id,
        # #238 (S6): 失敗 run の課金 (全損コスト) を集計可能にするため、notes 文字列
        # ではなく第一級フィールドとして記録する。blocked/failed でも消費額が残る。
        "total_cost_usd": total_cost_usd,
        "max_budget_usd_effective": effective_max_budget_usd,
        "burn_rate_usd_per_min": burn_rate_usd_per_min,
        "mission_profile": mission_profile if arm == "mission" else None,
        "started_at": started,
        "completed_at": completed,
        "starting_commit": starting_commit,
        "run_status": evaluation["run_status"],
        "blocked_reason": evaluation["blocked_reason"],
        "failure_kind": evaluation["failure_kind"],
        "comparable_attempt": evaluation["comparable_attempt"],
        "completion": evaluation["completion"],
        "validator_pass": evaluation["validator_pass"],
        "human_quality_score": evaluation["human_quality_score"],
        "quality_score_method": evaluation["quality_score_method"],
        "quality_marker_score": evaluation["quality_marker_score"],
        "quality_marker_score_raw": evaluation["quality_marker_score_raw"],
        "quality_marker_recall": evaluation["quality_marker_recall"],
        "forbidden_markers_matched": evaluation["forbidden_markers_matched"],
        "quality_markers_matched": evaluation["quality_markers_matched"],
        "quality_markers_missing": evaluation["quality_markers_missing"],
        "intervention_count": evaluation["intervention_count"],
        "resume_success": evaluation["resume_success"],
        "evidence_completeness": evaluation["evidence_completeness"],
        "validator_fraction": evaluation["validator_fraction"],
        "missing_arm_specific_headings": evaluation["missing_arm_specific_headings"],
        "permission_mode_degraded": detect_permission_degradation(stderr),
        "run_index": run_index,
        **mission_state_fields,
        "elapsed_minutes": elapsed,
        "token_estimate": input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None,
        "artifacts": artifacts,
        "notes": "; ".join(notes),
    }


PERMISSION_DEGRADATION_MARKER = "Permission mode forced"


def detect_permission_degradation(stderr: str) -> bool:
    """#268: 子 claude の stderr から permission-mode 降格警告を検出する.

    CC セッションの Bash から起動すると CLAUDE_CODE_SUBPROCESS_ENV_SCRUB が伝播し、
    --permission-mode acceptEdits が default に強制降格される (2026-07-23 監査)。
    """
    return PERMISSION_DEGRADATION_MARKER in (stderr or "")


# #292: CC 2.1.219 の allowed_non_write_users hardening は SCRUB=0 opt-out を無視して
# --permission-mode acceptEdits を default に強制降格する。警告文が案内する正規回避は
# allowedTools の明示。両 arm に同一リストを渡し、arm 間比較の妥当性を保つ。
CHILD_ALLOWED_TOOLS = "Read,Write,Edit,Grep,Glob,Bash,Agent,Skill,TodoWrite"


def resolve_max_budget_usd(
    arm: str,
    max_budget_usd: float | None = None,
    max_budget_usd_goal: float | None = None,
    max_budget_usd_mission: float | None = None,
) -> float:
    """Resolve the arm budget with specific -> common -> legacy-default precedence."""
    arm_budget = max_budget_usd_mission if arm == "mission" else max_budget_usd_goal
    if arm_budget is not None:
        return arm_budget
    if max_budget_usd is not None:
        return max_budget_usd
    return 2.0


def calculate_burn_rate_usd_per_min(
    run_status: str,
    total_cost_usd: float | None,
    elapsed_minutes: float,
) -> float | None:
    """Return a finite blocked-run burn rate when cost and duration are usable."""
    values = (total_cost_usd, elapsed_minutes)
    if run_status != "blocked":
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    if total_cost_usd < 0 or elapsed_minutes <= 0:
        return None
    return round(total_cost_usd / elapsed_minutes, 4)


def build_child_command(
    arm: str,
    prompt: str,
    max_budget_usd: float | None = None,
    max_budget_usd_goal: float | None = None,
    max_budget_usd_mission: float | None = None,
) -> list:
    """#292: 子 claude の argv を構築する (テスト可能な単一定義)."""
    effective_max_budget_usd = resolve_max_budget_usd(
        arm,
        max_budget_usd=max_budget_usd,
        max_budget_usd_goal=max_budget_usd_goal,
        max_budget_usd_mission=max_budget_usd_mission,
    )
    command = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        CHILD_ALLOWED_TOOLS,
        "--max-budget-usd",
        str(effective_max_budget_usd),
    ]
    if arm == "mission":
        command.extend(["--plugin-dir", str(MISSION_PLUGIN_DIR)])
    command.append(prompt)
    return command


def child_env(base_env: dict) -> dict:
    """#268: 子 claude プロセスの env。scrub を明示無効化して降格を防止する.

    使い捨て clone 内への書込のみのため opt-out が適切。
    """
    env = dict(base_env)
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "0"
    return env


def apply_mission_adherence_guard(
    status: dict, arm: str, mission_state_note: str | None,
    task_complexity: str | None = None,
) -> dict:
    """#261: mission ループ未初期化の mission record を無効化する.

    openworld-v1 で `.mission-state` を作らず素で回答した mission record が
    aggregate を希釈した実害への対策。state 破損 (unreadable) はループ開始の
    証拠があるため対象外。blocked は外的要因の分類を優先して保持する。
    """
    if arm != "mission":
        return status
    if mission_state_note != "mission_state_missing":
        return status
    if status.get("run_status") == "blocked":
        return status
    # #333: Simple タスクの state 不在は init 経路 routing (#276) の正規挙動。
    # invalid 化せず routed として扱う (mission_routed は呼び出し側で設定)。
    if task_complexity == "Simple":
        return status
    status = dict(status)
    status["run_status"] = "failed"
    status["failure_kind"] = "mission_loop_not_initialized"
    status["comparable_attempt"] = False
    return status


def execute_plan(entries, worker, parallel: int = 1, on_record=None, stop_on_blocked: bool = False):
    """#270: plan entries を worker pool で実行する。

    - parallel=1 は従来の逐次実行と同一順序・同一挙動 (後方互換)
    - record は完了順に返す (record 自身が task/arm/run_index を持つため順序は監査に影響しない)
    - on_record(entry, record) は record ごとに 1 回、lock 下で直列に呼ぶ (JSONL append 安全)
    - api_spend_limit は常に、その他は stop_on_blocked=True のとき、
      blocked record 検出後に未開始 entry を起動しない
      (実行中の entry は完走させ record も記録する)
    """
    import concurrent.futures

    records: list[dict] = []
    emit_lock = threading.Lock()
    stop_event = threading.Event()

    def _must_stop(record):
        if record.get("failure_kind") == "api_spend_limit":
            return True
        return stop_on_blocked and record.get("run_status") == "blocked"

    def _run_entry(entry):
        record = worker(entry)
        with emit_lock:
            records.append(record)
            if on_record is not None:
                on_record(entry, record)
            if _must_stop(record):
                stop_event.set()
        return record

    if parallel <= 1:
        for entry in entries:
            if stop_event.is_set():
                break
            _run_entry(entry)
        # 従来互換: blocked による早期停止経路に入ったら stopped_early=True
        return records, stop_event.is_set()

    entry_iter = iter(entries)
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        pending = {
            pool.submit(_run_entry, entry)
            for entry in (next(entry_iter, None) for _ in range(parallel))
            if entry is not None
        }
        while pending:
            done, pending = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                future.result()  # worker 例外を伝播させる
            while not stop_event.is_set() and len(pending) < parallel:
                entry = next(entry_iter, None)
                if entry is None:
                    break
                pending.add(pool.submit(_run_entry, entry))
    return records, stop_event.is_set()


def summarize(
    records: list[dict],
    tasks: list[dict],
    run_id: str,
    starting_commit: str,
    tasks_path: Path,
    stopped_early: bool = False,
    mission_profile: str = "full",
    repeats: int = 1,
) -> dict:
    by_arm: dict[str, list[dict]] = {arm: [r for r in records if r["arm"] == arm] for arm in ARMS}
    task_cohort = tasks_path.stem.removeprefix("tasks.")

    def _marker_variance(items: list[dict]) -> float | None:
        # #249: 母分散 (pvariance)。n<2 は分散なし (None)。
        values = [r["quality_marker_score"] for r in items if r.get("quality_marker_score") is not None]
        if len(values) < 2:
            return None
        return round(statistics.pvariance(values), 6)

    def _cost_values(items: list[dict]) -> list[float]:
        return [r["total_cost_usd"] for r in items if isinstance(r.get("total_cost_usd"), (int, float))]

    def _comp(items: list[dict]) -> list[dict]:
        # #261: comparable_attempt=False (無効 record) を除いた集計対象
        return [r for r in items if r.get("comparable_attempt", True)]

    expected_records = len(tasks) * len(ARMS) * max(1, repeats)
    spend_limit_record = next(
        (index for index, record in enumerate(records, start=1) if record.get("failure_kind") == "api_spend_limit"),
        None,
    )
    limitations = [
        "Claude Code print mode smoke; does not fully exercise multi-turn interactive /goal persistence.",
        "Quality and evidence scores are automated heuristic scores, not blind human review.",
        "Blocked records are excluded from comparable quality-marker aggregates.",
    ]
    if any(r.get("permission_mode_degraded") for r in records):
        limitations.append(
            "WARNING: permission-mode degradation detected in one or more records; "
            "acceptEdits was forced to default (see #268). Cross-run comparability is affected."
        )
    if any(r.get("mission_evidence_only") for r in records):
        limitations.append(
            "WARNING: one or more mission-arm records halted by submitting evidence "
            "without a scored review loop (#341); their wall-clock understates the "
            "gated loop and is not comparable to full-loop records."
        )
    if any(r.get("failure_kind") == "max_budget_usd" for r in records):
        limitations.append(
            "WARNING: one or more records were blocked by max_budget_usd; "
            "review per-arm burn rate before interpreting cost or completion results."
        )
    limitations.append(
        "total_cost_usd is an API-equivalent estimate reported by the agent runtime, not a billed amount. "
        "Under subscription (OAuth) execution no per-token charge is incurred; the consumed resource is the "
        "plan rate limit. Use these values for relative comparison between arms, not as an absolute spend figure."
    )
    if spend_limit_record is not None:
        limitations.append(
            "WARNING: API spend limit affected one or more records; affected records are "
            "excluded from comparable aggregates and cross-run comparability is affected."
        )
        if stopped_early:
            limitations.append(
                f"run stopped early: api_spend_limit at record {spend_limit_record}/{expected_records}"
            )

    # --- Saturation guard (#563) ---
    def _scored(items: list[dict]) -> list[dict]:
        return [r for r in items if r.get("quality_marker_score") is not None]

    all_scored = _scored(records)
    marker_saturated = bool(all_scored) and all(r["quality_marker_score"] >= 1.0 for r in all_scored)
    marker_saturation_detail = {
        arm: {r.get("task_id", "unknown"): r["quality_marker_score"] for r in _scored(items) if r["quality_marker_score"] >= 1.0}
        for arm, items in by_arm.items()
    }
    measurement_valid: bool
    measurement_valid_reason: str
    if not all_scored:
        measurement_valid = False
        measurement_valid_reason = "no scored records"
    elif marker_saturated:
        measurement_valid = False
        measurement_valid_reason = "marker_saturated"
    elif len({r["quality_marker_score"] for r in all_scored}) == 1:
        # 天井が 1.0 から移動しただけの場合を取りこぼさない。#557 が問題に
        # しているのは弁別できないことであり、値が満点であることではない。
        measurement_valid = False
        measurement_valid_reason = "no_discrimination"
    else:
        measurement_valid = True
        measurement_valid_reason = "ok"

    def _arm_mean(arm: str) -> float | None:
        vals = [r["quality_marker_score"] for r in _scored(by_arm.get(arm, [])) ]
        return round(sum(vals) / len(vals), 4) if vals else None

    goal_mean = _arm_mean("claude_code_goal_command")
    mission_mean = _arm_mean("mission")
    marker_score_delta_vs_goal: float | None = (
        round(mission_mean - goal_mean, 4)
        if mission_mean is not None and goal_mean is not None
        else None
    )
    marker_score_ratio_vs_goal: float | None = (
        round(mission_mean / goal_mean, 4)
        if mission_mean is not None and goal_mean is not None and goal_mean != 0
        else None
    )
    # --- end saturation guard ---

    iteration_rows = [
        iteration
        for record in by_arm["mission"]
        for iteration in (record.get("diff_review_observations") or {}).get("iterations", [])
        if isinstance(iteration, dict)
    ]
    eligible_iter2_records = [
        record for record in by_arm["mission"]
        if record.get("run_status") == "completed"
        and record.get("comparable_attempt") is True
        and record.get("failure_kind") is None
        and record.get("permission_mode_degraded") is False
        and isinstance(record.get("mission_iterations"), int)
        and not isinstance(record.get("mission_iterations"), bool)
        and record["mission_iterations"] >= 2
        and isinstance(record.get("diff_review_observations"), dict)
        and record["diff_review_observations"].get("status") == "observed"
        and isinstance(record["diff_review_observations"].get("critic_has_new_scope"), bool)
        and any(
            _positive_iteration(row.get("iteration")) is not None
            and row["iteration"] >= 2
            and row.get("wall_clock_status") == "observed"
            and _has_positive_activity_duration(row)
            for row in record["diff_review_observations"].get("iterations", [])
            if isinstance(row, dict)
        )
    ]
    iter2_record_costs = [
        record.get("total_cost_usd") for record in eligible_iter2_records
        if isinstance(record.get("total_cost_usd"), (int, float))
        and not isinstance(record.get("total_cost_usd"), bool)
        and math.isfinite(record["total_cost_usd"])
        and record["total_cost_usd"] >= 0
    ]
    diff_review_measurement_gate = {
        "iter2_eligible_records": len(eligible_iter2_records),
        "iter2_record_cost_usd_total": round(sum(iter2_record_costs), 6) if iter2_record_costs else None,
        "iter2_record_cost_usd_mean": round(sum(iter2_record_costs) / len(iter2_record_costs), 6) if iter2_record_costs else None,
        "permission_degraded_records": sum(1 for record in records if record.get("permission_mode_degraded")),
        "mission_loop_not_initialized_records": sum(
            1 for record in records if record.get("failure_kind") == "mission_loop_not_initialized"
        ),
        "context_manifest_expected_iterations": sum(
            1 for iteration in iteration_rows if iteration.get("context_mode_expected") == "bounded"
        ),
        "context_manifest_generated_iterations": sum(
            1 for iteration in iteration_rows if iteration.get("context_manifest_generated") is True
        ),
        "context_manifest_fallback_iterations": sum(
            1 for iteration in iteration_rows if iteration.get("context_manifest_fallback") is True
        ),
    }

    return {
        "run_id": run_id,
        "task_file": str(tasks_path.relative_to(REPO_ROOT)),
        "task_cohort": task_cohort,
        "selected_task_ids": [task["id"] for task in tasks],
        "mission_profile": mission_profile,
        "starting_commit": starting_commit,
        "records": len(records),
        "repeats": repeats,
        "expected_records": expected_records,
        "stopped_early": stopped_early,
        "limitations": limitations,
        "marker_saturated": marker_saturated,
        "marker_saturation_detail": marker_saturation_detail,
        "measurement_valid": measurement_valid,
        "measurement_valid_reason": measurement_valid_reason,
        "marker_score_delta_vs_goal": marker_score_delta_vs_goal,
        "marker_score_ratio_vs_goal": marker_score_ratio_vs_goal,
        "diff_review_measurement_gate": diff_review_measurement_gate,
        "benchmark_kpi": summarize_benchmark_kpi(records),
        "arms": {
            arm: {
                "records": len(items),
                "blocked_records": sum(1 for r in items if r.get("run_status") == "blocked"),
                "budget_blocked_records": sum(
                    1 for r in items if r.get("failure_kind") == "max_budget_usd"
                ),
                "api_spend_limit_records": sum(
                    1 for r in items if r.get("failure_kind") == "api_spend_limit"
                ),
                "permission_degraded_records": sum(1 for r in items if r.get("permission_mode_degraded")),
                "routed_records": sum(1 for r in items if r.get("mission_routed")),
                "evidence_only_records": sum(1 for r in items if r.get("mission_evidence_only")),
                "comparable_records": sum(1 for r in items if r.get("comparable_attempt", True)),
                "completion_rate": sum(1 for r in items if r["completion"]) / len(items) if items else None,
                "comparable_completion_rate": (
                    sum(1 for r in items if r.get("comparable_attempt", True) and r["completion"])
                    / sum(1 for r in items if r.get("comparable_attempt", True))
                    if sum(1 for r in items if r.get("comparable_attempt", True))
                    else None
                ),
                "validator_pass_rate": sum(1 for r in items if r["validator_pass"]) / len(items) if items else None,
                "comparable_validator_pass_rate": (
                    sum(1 for r in items if r.get("comparable_attempt", True) and r["validator_pass"])
                    / sum(1 for r in items if r.get("comparable_attempt", True))
                    if sum(1 for r in items if r.get("comparable_attempt", True))
                    else None
                ),
                "average_quality_score": round(sum(r["human_quality_score"] for r in items) / len(items), 2) if items else None,
                "average_intervention_count": round(sum(r["intervention_count"] for r in items) / len(items), 2) if items else None,
                "average_evidence_completeness": round(sum(r["evidence_completeness"] for r in items) / len(items), 2) if items else None,
                "average_quality_marker_score": (
                    round(
                        sum(r["quality_marker_score"] for r in items if r.get("quality_marker_score") is not None)
                        / len([r for r in items if r.get("quality_marker_score") is not None]),
                        2,
                    )
                    if [r for r in items if r.get("quality_marker_score") is not None]
                    else None
                ),
                "average_elapsed_minutes": round(sum(r["elapsed_minutes"] for r in items) / len(items), 2) if items else None,
                # #261: comparable record のみの品質・速度・コスト。無効 record
                # (mission_loop_not_initialized 等) による希釈を防ぐ。既存フィールドは
                # 全 records の歴史的意味 (全損コスト込み) を維持する。
                "comparable_average_quality_score": (
                    round(sum(r["human_quality_score"] for r in _comp(items)) / len(_comp(items)), 2)
                    if _comp(items) else None
                ),
                "comparable_average_elapsed_minutes": (
                    round(sum(r["elapsed_minutes"] for r in _comp(items)) / len(_comp(items)), 2)
                    if _comp(items) else None
                ),
                "comparable_cost_usd_mean": (
                    round(sum(_cost_values(_comp(items))) / len(_cost_values(_comp(items))), 4)
                    if _cost_values(_comp(items)) else None
                ),
                # #249: 反復時の分散とコスト集計 (blocked/failed の全損コストも含む)。
                "marker_score_variance": _marker_variance(items),
                # #563: 飽和検知 / 分散可視化
                "marker_score_min": (
                    min(r["quality_marker_score"] for r in _scored(items))
                    if _scored(items) else None
                ),
                "marker_score_max": (
                    max(r["quality_marker_score"] for r in _scored(items))
                    if _scored(items) else None
                ),
                "marker_score_distinct_values": len({r["quality_marker_score"] for r in _scored(items)}),
                "cost_usd_total": round(sum(_cost_values(items)), 4) if _cost_values(items) else None,
                "cost_usd_mean": (
                    round(sum(_cost_values(items)) / len(_cost_values(items)), 4)
                    if _cost_values(items)
                    else None
                ),
            }
            for arm, items in by_arm.items()
        },
    }


def summary_warnings(summary: dict) -> list[str]:
    """Return warning lines for the given summary dict.

    Designed for extension: issue #565 can append additional warning lines here.
    Callers should print each returned line before other summary output.
    """
    warnings: list[str] = []
    if not summary.get("measurement_valid", True):
        reason = summary.get("measurement_valid_reason", "")
        if reason == "marker_saturated":
            warnings.append(
                "WARNING: quality markers are saturated (all records scored 1.0). This run cannot\n"
                "discriminate quality between arms. Cost/time comparisons remain valid; quality\n"
                "comparison is NOT valid. See issue #557."
            )
        elif reason == "no_discrimination":
            warnings.append(
                "WARNING: every record scored the same marker value, so the metric cannot\n"
                "discriminate quality between arms even though it is not at the 1.0 ceiling.\n"
                "Cost/time comparisons remain valid; quality comparison is NOT valid. See issue #557."
            )
        elif reason == "no scored records":
            warnings.append(
                "WARNING: no scored records found. Quality comparison is NOT valid."
            )
        else:
            warnings.append(
                f"WARNING: measurement_valid is false (reason: {reason}). Quality comparison is NOT valid."
            )
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_PATH))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--limit-tasks", type=int, default=2)
    parser.add_argument(
        "--task-ids",
        default=None,
        help="Comma-separated task ids to run. Overrides --limit-tasks and avoids rerunning earlier tasks.",
    )
    parser.add_argument(
        "--stop-on-blocked",
        action="store_true",
        help="Stop the run after the first blocked record to conserve API budget.",
    )
    parser.add_argument("--run-root", default="/tmp/mission-vs-official-goal")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="#249: 各 (task, arm) セルを N 回反復する。run_index を record に記録し、summary に分散を出す。",
    )
    parser.add_argument(
        "--model-id",
        required=True,
        help="Truthful model identifier for this run (e.g. claude-opus-4-8). "
        "Recorded verbatim; there is no silent 'unknown' fallback.",
    )
    parser.add_argument("--max-budget-usd", type=float, default=None)
    parser.add_argument("--max-budget-usd-goal", type=float, default=None)
    parser.add_argument("--max-budget-usd-mission", type=float, default=None)
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="#270: 同時実行 worker 数。record は独立 clone で隔離済みのため並列可。"
        " rate limit 配慮で 2-3 推奨。default 1 は従来の逐次実行と同一挙動。",
    )
    parser.add_argument("--mission-max-iter", type=int, default=None)
    parser.add_argument(
        "--mission-budget-minutes",
        type=float,
        default=None,
        help="#238: /mission へ --budget-minutes として渡す時間予算 (分)。"
        " budget pressure による graceful partial-done halt を有効化する。",
    )
    parser.add_argument(
        "--mission-profile",
        choices=MISSION_PROFILES,
        default="full",
        help="Mission prompt profile. 'light' reduces planning/review scope for cost-controlled comparisons.",
    )
    args = parser.parse_args()
    for flag, value in (
        ("--max-budget-usd", args.max_budget_usd),
        ("--max-budget-usd-goal", args.max_budget_usd_goal),
        ("--max-budget-usd-mission", args.max_budget_usd_mission),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"{flag} must be a positive finite number")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")

    tasks_path = Path(args.tasks_file)
    if not tasks_path.is_absolute():
        tasks_path = REPO_ROOT / tasks_path

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{args.run_id}.jsonl"
    summary_path = RESULTS_DIR / f"{args.run_id}-summary.json"
    artifact_run_dir = ARTIFACTS_DIR / args.run_id
    if result_path.exists():
        result_path.unlink()
    if summary_path.exists():
        summary_path.unlink()
    if artifact_run_dir.exists():
        shutil.rmtree(artifact_run_dir)

    task_data = load_task_data(tasks_path)
    tasks = select_tasks(task_data, args.limit_tasks, args.task_ids)

    def _worker(entry):
        task, arm, arm_order, run_index = entry
        print(f"running task={task['id']} arm={arm} rep={run_index}", flush=True)
        return run_one(
            task,
            arm,
            args.run_id,
            args.starting_commit,
            Path(args.run_root),
            args.timeout,
            args.max_budget_usd,
            args.mission_max_iter,
            args.mission_profile,
            arm_order,
            args.model_id,
            mission_budget_minutes=args.mission_budget_minutes,
            hidden_paths=task_data.get("hidden_paths"),
            extra_rules=task_data.get("prompt_rules", ()),
            run_index=run_index,
            repeats=args.repeats,
            max_budget_usd_goal=args.max_budget_usd_goal,
            max_budget_usd_mission=args.max_budget_usd_mission,
        )

    def _on_record(entry, record):
        task, arm, _arm_order, run_index = entry
        with result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        print(
            f"finished task={task['id']} arm={arm} rep={run_index} completion={record['completion']} "
            f"validator_pass={record['validator_pass']} elapsed_minutes={record['elapsed_minutes']}",
            flush=True,
        )
        if record.get("failure_kind") == "api_spend_limit":
            print(
                f"stopping early after API spend limit task={task['id']} arm={arm}",
                flush=True,
            )
        elif args.stop_on_blocked and record["run_status"] == "blocked":
            print(
                f"stopping early after blocked record task={task['id']} arm={arm} "
                f"blocked_reason={record['blocked_reason']}",
                flush=True,
            )

    # #270: 独立 record を worker pool で実行 (default 1 = 従来の逐次と同一挙動)
    records, stopped_early = execute_plan(
        expanded_plan(tasks, ARMS, args.repeats),
        _worker,
        parallel=args.parallel,
        on_record=_on_record,
        stop_on_blocked=args.stop_on_blocked,
    )

    summary = summarize(
        records,
        tasks,
        args.run_id,
        args.starting_commit,
        tasks_path,
        stopped_early=stopped_early,
        mission_profile=args.mission_profile,
        repeats=args.repeats,
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for warning in summary_warnings(summary):
        print(warning)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
