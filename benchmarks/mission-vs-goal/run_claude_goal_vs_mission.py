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
import shutil
import subprocess
import sys
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

    total = len(markers)
    ratio = len(matched) / total if total else None
    return {
        "quality_markers_total": total,
        "quality_markers_matched": matched,
        "quality_markers_missing": missing,
        "quality_marker_score": round(ratio, 2) if ratio is not None else None,
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


def build_prompt(
    task: dict,
    arm: str,
    output_rel: str,
    mission_max_iter: int | None = None,
    mission_profile: str = "full",
) -> str:
    common = f"""You are executing one controlled local benchmark run.

Rules:
- Do not commit, push, install packages, or use network access.
- Write exactly one task artifact at `{output_rel}`.
- Keep edits narrowly scoped to benchmark output files. For the mission arm, `.mission-state/` is also allowed.
- Do not claim benchmark superiority. Only complete this task artifact.
- Include concrete evidence for every claim. If something is unmeasured, say it is unmeasured.

Task id: {task["id"]}
Task category: {task["category"]}
Task prompt: {task["prompt"]}
Task validator: {task["validator"]}
"""
    marker_names = quality_marker_names(task)
    if marker_names:
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
    return f"""/mission Complete the controlled benchmark artifact at `{output_rel}` with auditable mission-style evidence. --max-iter {mission_max_iter}

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


def score_from_signals(validator_pass: bool, marker_score: float | None) -> tuple[float, float]:
    """Deterministic, arm-blind scoring (F-1).

    The arm identity is never consulted here; the same artifact signals produce
    the same score for any arm. Returns (quality_score, evidence_completeness).
        quality = 1.0 + 3.0 * validator_pass + 1.0 * (marker_score or 0.0)
    Marker-less tasks (marker_score is None -> treated as 0.0) collapse to 1.0
    (fail) or 4.0 (pass).
    """
    base = marker_score if marker_score is not None else 0.0
    value = round(1.0 + 3.0 * (1.0 if validator_pass else 0.0) + 1.0 * base, 2)
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
    required_headings = ["Evidence", "Assumptions"]
    if arm == "claude_code_goal_command":
        required_headings += ["Goal", "Result", "Stop Condition"]
    else:
        required_headings += ["Mission", "Plan", "Execution", "Review", "Score", "Stop Decision"]

    missing_headings = [heading for heading in required_headings if heading not in text]
    completion = returncode == 0 and output_path.exists()
    validator_pass = completion and not missing_headings
    marker_eval = evaluate_quality_markers(text, task)
    if not validator_pass:
        marker_eval["quality_marker_score"] = None
    marker_ratio = marker_eval["quality_marker_score"]
    quality_score, evidence_score = score_from_signals(validator_pass, marker_ratio)
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
        "quality_score_method": "automated_heuristic_not_blind_human",
        "intervention_count": 0,
        "resume_success": None if "resume" not in task["id"] else validator_pass,
        "evidence_completeness": evidence_score,
        "missing_headings": missing_headings,
        "artifact_bytes": output_path.stat().st_size if output_path.exists() else 0,
        **marker_eval,
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
) -> dict:
    task_id = task["id"]
    run_name = f"{task_id}-{arm}"
    worktree = run_root / run_name / "repo"
    prepare_clone(REPO_ROOT, worktree, starting_commit)
    output_rel = f"benchmarks/mission-vs-goal/run-output/{run_id}/{run_name}.md"
    prompt = build_prompt(task, arm, output_rel, mission_max_iter=mission_max_iter, mission_profile=mission_profile)
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
        "quality_markers_matched": evaluation["quality_markers_matched"],
        "quality_markers_missing": evaluation["quality_markers_missing"],
        "intervention_count": evaluation["intervention_count"],
        "resume_success": evaluation["resume_success"],
        "evidence_completeness": evaluation["evidence_completeness"],
        "elapsed_minutes": elapsed,
        "token_estimate": input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None,
        "artifacts": artifacts,
        "notes": "; ".join(notes),
    }


def summarize(
    records: list[dict],
    tasks: list[dict],
    run_id: str,
    starting_commit: str,
    tasks_path: Path,
    stopped_early: bool = False,
    mission_profile: str = "full",
) -> dict:
    by_arm: dict[str, list[dict]] = {arm: [r for r in records if r["arm"] == arm] for arm in ARMS}
    task_cohort = tasks_path.stem.removeprefix("tasks.")
    return {
        "run_id": run_id,
        "task_file": str(tasks_path.relative_to(REPO_ROOT)),
        "task_cohort": task_cohort,
        "selected_task_ids": [task["id"] for task in tasks],
        "mission_profile": mission_profile,
        "starting_commit": starting_commit,
        "records": len(records),
        "expected_records": len(tasks) * len(ARMS),
        "stopped_early": stopped_early,
        "limitations": [
            "Claude Code print mode smoke; does not fully exercise multi-turn interactive /goal persistence.",
            "Quality and evidence scores are automated heuristic scores, not blind human review.",
            "Blocked records are excluded from comparable quality-marker aggregates.",
        ],
        "arms": {
            arm: {
                "records": len(items),
                "blocked_records": sum(1 for r in items if r.get("run_status") == "blocked"),
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
        "--model-id",
        required=True,
        help="Truthful model identifier for this run (e.g. claude-opus-4-8). "
        "Recorded verbatim; there is no silent 'unknown' fallback.",
    )
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--mission-max-iter", type=int, default=None)
    parser.add_argument(
        "--mission-profile",
        choices=MISSION_PROFILES,
        default="full",
        help="Mission prompt profile. 'light' reduces planning/review scope for cost-controlled comparisons.",
    )
    args = parser.parse_args()

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
    records = []
    stopped_early = False
    for task, arm, arm_order in counterbalanced_plan(tasks, ARMS):
        print(f"running task={task['id']} arm={arm}", flush=True)
        record = run_one(
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
        )
        records.append(record)
        with result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        print(
            f"finished task={task['id']} arm={arm} completion={record['completion']} "
            f"validator_pass={record['validator_pass']} elapsed_minutes={record['elapsed_minutes']}",
            flush=True,
        )
        if args.stop_on_blocked and record["run_status"] == "blocked":
            stopped_early = True
            print(
                f"stopping early after blocked record task={task['id']} arm={arm} "
                f"blocked_reason={record['blocked_reason']}",
                flush=True,
            )
            break

    summary = summarize(
        records,
        tasks,
        args.run_id,
        args.starting_commit,
        tasks_path,
        stopped_early=stopped_early,
        mission_profile=args.mission_profile,
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
