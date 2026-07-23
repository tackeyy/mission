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
import json
import os
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
MAX_BUDGET_MARKERS = (
    "error_max_budget_usd",
    "max_budget_usd",
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


def quality_marker_patterns(marker: str | dict) -> list[str]:
    if isinstance(marker, dict):
        values = marker.get("patterns") or [marker["name"]]
    else:
        values = [marker]
    return [str(value).lower() for value in values]


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


def evaluate_quality_markers(text: str, task: dict) -> dict:
    markers = task.get("quality_markers", [])
    lowered = text.lower()
    matched: list[str] = []
    missing: list[str] = []
    for marker in markers:
        name = str(marker["name"] if isinstance(marker, dict) else marker)
        patterns = quality_marker_patterns(marker)
        if any(pattern in lowered for pattern in patterns):
            matched.append(name)
        else:
            missing.append(name)

    forbidden_matched: list[str] = []
    for marker in task.get("forbidden_markers", []):
        name = str(marker["name"] if isinstance(marker, dict) else marker)
        patterns = quality_marker_patterns(marker)
        if any(pattern in lowered for pattern in patterns):
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
    return f"""/mission Complete the controlled benchmark artifact at `{output_rel}` with auditable mission-style evidence. --max-iter {mission_max_iter}{budget_flag}

{common}
Arm: mission
Mission profile: {mission_profile}

Use the `/mission` plugin workflow with auditable state. Initialize or maintain
mission state if needed, review the artifact against the validator, and only
report completion when the artifact is written.
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


def classify_run_status(
    stdout: str,
    stderr: str,
    timed_out: bool,
    returncode: int,
    output_exists: bool,
    validator_pass: bool,
) -> dict:
    combined = f"{stdout}\n{stderr}"
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
    if any(marker in combined for marker in MAX_BUDGET_MARKERS):
        return {
            "run_status": "blocked",
            "blocked_reason": "max_budget_usd",
            "failure_kind": "max_budget_usd",
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
    }
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
    return fields, None


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
            "automated_heuristic_form_stripped_gradient_v2_not_blind_human"
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
    max_budget_usd: float,
    mission_max_iter: int | None,
    mission_profile: str,
    arm_order: int,
    model_id: str,
    mission_budget_minutes: float | None = None,
    hidden_paths: list[str] | None = None,
    extra_rules: list[str] | tuple[str, ...] = (),
    run_index: int = 1,
    repeats: int = 1,
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

    command = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    if arm == "mission":
        command.extend(["--plugin-dir", str(MISSION_PLUGIN_DIR)])
    command.append(prompt)

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
            },
            None,
        )
    # #261: mission ループ未初期化の record を無効化 (aggregate 希釈防止)
    guarded = apply_mission_adherence_guard(
        {k: evaluation[k] for k in ("run_status", "blocked_reason", "failure_kind", "comparable_attempt")},
        arm=arm,
        mission_state_note=mission_state_note,
    )
    evaluation.update(guarded)
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
        "total_cost_usd": (
            claude_result.get("total_cost_usd") if isinstance(claude_result, dict) else None
        ),
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


def child_env(base_env: dict) -> dict:
    """#268: 子 claude プロセスの env。scrub を明示無効化して降格を防止する.

    使い捨て clone 内への書込のみのため opt-out が適切。
    """
    env = dict(base_env)
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "0"
    return env


def apply_mission_adherence_guard(status: dict, arm: str, mission_state_note: str | None) -> dict:
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
    - stop_on_blocked=True: blocked record 検出後、未開始 entry を起動しない
      (実行中の entry は完走させ record も記録する)
    """
    import concurrent.futures

    records: list[dict] = []
    stopped = False
    emit_lock = threading.Lock()
    stop_event = threading.Event()

    def _run_entry(entry):
        record = worker(entry)
        with emit_lock:
            records.append(record)
            if on_record is not None:
                on_record(entry, record)
        if stop_on_blocked and record.get("run_status") == "blocked":
            stop_event.set()
        return record

    if parallel <= 1:
        for entry in entries:
            if stop_event.is_set():
                break
            _run_entry(entry)
        # 従来互換: blocked による早期停止経路に入ったら stopped_early=True
        return records, stop_event.is_set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = []
        for entry in entries:
            if stop_event.is_set():
                break
            futures.append(pool.submit(_run_entry, entry))
        for f in concurrent.futures.as_completed(futures):
            f.result()  # worker 例外を伝播させる
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

    return {
        "run_id": run_id,
        "task_file": str(tasks_path.relative_to(REPO_ROOT)),
        "task_cohort": task_cohort,
        "selected_task_ids": [task["id"] for task in tasks],
        "mission_profile": mission_profile,
        "starting_commit": starting_commit,
        "records": len(records),
        "repeats": repeats,
        "expected_records": len(tasks) * len(ARMS) * max(1, repeats),
        "stopped_early": stopped_early,
        "limitations": [
            "Claude Code print mode smoke; does not fully exercise multi-turn interactive /goal persistence.",
            "Quality and evidence scores are automated heuristic scores, not blind human review.",
            "Blocked records are excluded from comparable quality-marker aggregates.",
        ] + ([
            "WARNING: permission-mode degradation detected in one or more records; "
            "acceptEdits was forced to default (see #268). Cross-run comparability is affected.",
        ] if any(r.get("permission_mode_degraded") for r in records) else []),
        "arms": {
            arm: {
                "records": len(items),
                "blocked_records": sum(1 for r in items if r.get("run_status") == "blocked"),
                "permission_degraded_records": sum(1 for r in items if r.get("permission_mode_degraded")),
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
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
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
        if args.stop_on_blocked and record["run_status"] == "blocked":
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
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
