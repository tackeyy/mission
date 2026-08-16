#!/usr/bin/env python3
"""Generate the A1 lifecycle golden fixture from an explicit legacy CLI.

This is a maintainer-only generator, not part of the test execution path. Pass
the extraction-predecessor ``mission-state.py`` explicitly; the generator never
searches git history. The checked-in golden file records the exact source
revision used for the one-time capture.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable


FIXTURE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = FIXTURE_DIR.parents[2]
TESTS_ROOT = SKILL_ROOT / "tests"
DEFAULT_OUTPUT = FIXTURE_DIR / "golden.json"
ROOT_TOKEN = "__ROOT__"
FIXED_PID = 424242
FIXED_HOSTNAME = "fixture-host"

sys.path.insert(0, str(SKILL_ROOT))
from tests.mission_state_fixture_corpus import (  # noqa: E402
    _write_core_plan,
    canonical_json_bytes,
    issue483_corpus,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-cli",
        type=Path,
        required=True,
        help="Explicit extraction-predecessor mission-state.py path",
    )
    parser.add_argument(
        "--source-revision",
        required=True,
        help="Immutable source revision recorded as fixture provenance",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def _normalize(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        normalized = {key: _normalize(item, root) for key, item in value.items()}
        for key in ("pid", "old_pid", "new_pid", "updated_by_pid"):
            if isinstance(normalized.get(key), int):
                normalized[key] = FIXED_PID
        if isinstance(normalized.get("hostname"), str):
            normalized["hostname"] = FIXED_HOSTNAME
        for key in ("host_run_id", "root_run_id"):
            if isinstance(normalized.get(key), str) and normalized[key].startswith("mission-local-"):
                normalized[key] = "mission-local-<generated>"
        if isinstance(normalized.get("handoff_id"), str) and normalized[
            "handoff_id"
        ].startswith("handoff_"):
            normalized["handoff_id"] = "handoff_<generated>"
        decision = normalized.get("specialists_decision")
        if isinstance(decision, dict) and isinstance(decision.get("selection_id"), str):
            decision["selection_id"] = "sel_<generated>"
        if isinstance(normalized.get("assumptions_path"), str):
            normalized["assumptions_path"] = "<generated-assumptions-path>"
        return normalized
    if isinstance(value, list):
        return [_normalize(item, root) for item in value]
    if isinstance(value, str):
        for root_form in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
            value = value.replace(root_form, ROOT_TOKEN)
        return value
    return value


def _state_bytes(root: Path) -> str | None:
    state_path = root / ".mission-state" / "sessions" / "test.json"
    if not state_path.exists():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return base64.b64encode(canonical_json_bytes(_normalize(payload, root))).decode("ascii")


def _normalize_output(text: str, root: Path) -> str:
    if not text:
        return text
    stripped = text.rstrip("\n")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return text.replace(str(root), ROOT_TOKEN)
    suffix = "\n" if text.endswith("\n") else ""
    return json.dumps(_normalize(payload, root), ensure_ascii=False) + suffix


def _run_cli(
    cli: Path,
    root: Path,
    arguments: tuple[str, ...],
    *,
    now: str,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    environment.update(
        {
            "MISSION_SESSION_ID": "test",
            "MISSION_LEASE_ID": "fixture-lease",
            "MISSION_STATE_NOW": now,
        }
    )
    return subprocess.run(
        [sys.executable, str(cli), *arguments],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _write_state(root: Path, payload: dict[str, Any]) -> None:
    state_path = root / ".mission-state" / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = copy.deepcopy(payload)
    state["project_root"] = str(root)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _capture(
    cli: Path,
    root: Path,
    arguments: tuple[str, ...],
    *,
    now: str,
) -> dict[str, Any]:
    before = _state_bytes(root)
    result = _run_cli(cli, root, arguments, now=now)
    return {
        "arguments": list(_normalize(list(arguments), root)),
        "environment": {"MISSION_STATE_NOW": now},
        "before_state_bytes_b64": before,
        "after_state_bytes_b64": _state_bytes(root),
        "stdout": _normalize_output(result.stdout, root),
        "stderr": _normalize_output(result.stderr, root),
        "exit_code": result.returncode,
    }


def _case(
    runs: Path,
    cli: Path,
    name: str,
    steps: list[tuple[tuple[str, ...], str]],
    *,
    prepare: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    root = (runs / name.replace("/", "-")).resolve()
    root.mkdir(parents=True)
    if prepare is not None:
        prepare(root)
    return {
        "steps": [
            _capture(cli, root, arguments, now=now)
            for arguments, now in steps
        ]
    }


def _build_cases(runs: Path, cli: Path) -> dict[str, Any]:
    cases: dict[str, Any] = {}

    cases["init_repository"] = _case(
        runs,
        cli,
        "init_repository",
        [
            (
                (
                    "init",
                    "A1 init parity",
                    "--complexity",
                    "Standard",
                    "--host-run-id",
                    "host-run",
                    "--root-run-id",
                    "root-run",
                    "--artifact-applicability",
                    "not-applicable",
                ),
                "2026-08-16T00:00:00Z",
            )
        ],
    )

    action_specs = {
        "init": (
            (
                "init",
                "A1 corpus replacement",
                "--complexity",
                "Standard",
                "--host-run-id",
                "host-run",
                "--root-run-id",
                "root-run",
                "--artifact-applicability",
                "not-applicable",
            ),
            0,
        ),
        "advance": (
            ("advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable"),
            2,
        ),
        "halt": (("mark-halt", "--reason", "compatibility halt", "--category", "other"), 0),
    }
    for action, (arguments, expected_exit) in action_specs.items():
        for label, payload in issue483_corpus().items():
            name = f"issue483/{action}/{label}"
            case = _case(
                runs,
                cli,
                name,
                [(arguments, "2026-08-16T00:10:00Z")],
                prepare=lambda root, payload=payload: _write_state(root, payload),
            )
            assert case["steps"][0]["exit_code"] == expected_exit, name
            cases[name] = case

    init_halt_reason = (
        "init",
        "A1 halt reason parity",
        "--complexity",
        "Standard",
        "--host-run-id",
        "host-run",
        "--root-run-id",
        "root-run",
    )
    for label, reason in (("surrounding-whitespace", " padded reason "), ("empty", "")):
        cases[f"mark_halt_reason/{label}"] = _case(
            runs,
            cli,
            f"mark_halt_reason/{label}",
            [
                (init_halt_reason, "2026-08-16T00:20:00Z"),
                (("mark-halt", "--reason", reason, "--category", "other"), "2026-08-16T00:20:00Z"),
            ],
        )

    cases["activity_start"] = _case(
        runs,
        cli,
        "activity_start",
        [
            (("init", "A1 activity parity", "--complexity", "Standard"), "2026-08-16T01:02:03Z"),
            (("activity", "start", "--kind", "active", "--reason", "implementation", "--at", "2026-08-16T01:02:03Z"), "2026-08-16T01:02:03Z"),
        ],
    )
    cases["activity_end"] = _case(
        runs,
        cli,
        "activity_end",
        [
            (("init", "A1 activity end parity", "--complexity", "Standard"), "2026-08-16T01:02:03Z"),
            (("activity", "start", "--kind", "active", "--reason", "implementation", "--at", "2026-08-16T01:02:03Z"), "2026-08-16T01:02:03Z"),
            (("activity", "end", "--at", "2026-08-16T01:03:05Z"), "2026-08-16T01:03:05Z"),
        ],
    )

    advance_root = (runs / "advance").resolve()
    advance_root.mkdir()
    plan_source = _write_core_plan(advance_root)
    cases["advance"] = {
        "steps": [
            _capture(cli, advance_root, ("init", "A1 advance parity", "--complexity", "Standard"), now="2026-08-16T02:00:00Z"),
            _capture(cli, advance_root, ("planning", "adopt-core", "--input", str(plan_source), "--source-id", "issue506-core"), now="2026-08-16T02:00:00Z"),
            _capture(cli, advance_root, ("advance", "--phase", "executing", "--activity", "active:implementation", "--at", "2026-08-16T02:00:00Z"), now="2026-08-16T02:00:00Z"),
            _capture(cli, advance_root, ("advance", "--phase", "reviewing", "--activity", "active:review", "--artifact-applicability", "not-applicable", "--at", "2026-08-16T02:04:06Z"), now="2026-08-16T02:04:06Z"),
        ]
    }

    simple_cases = {
        "mark_halt": [
            (("init", "A1 halt parity", "--complexity", "Standard"), "2026-08-16T03:05:07Z"),
            (("mark-halt", "--reason", "waiting for an external prerequisite", "--category", "blocked-external"), "2026-08-16T03:05:07Z"),
        ],
        "aggregate_failure": [
            (("init", "A1 aggregate failure", "--complexity", "Standard"), "2026-08-16T03:06:08Z"),
        ],
        "reactivate": [
            (("init", "A1 reactivate parity", "--complexity", "Standard"), "2026-08-16T04:00:00Z"),
            (("mark-halt", "--reason", "waiting for approval", "--category", "awaiting-approval"), "2026-08-16T04:00:00Z"),
            (("reactivate", "--approved-by-user", "--reason", "approval was recorded", "--expected-category", "awaiting-approval", "--phase", "planning"), "2026-08-16T04:02:04Z"),
        ],
        "refresh_pid": [
            (("init", "A1 refresh parity", "--complexity", "Standard"), "2026-08-16T05:01:03Z"),
            (("refresh-pid",), "2026-08-16T05:01:03Z"),
        ],
        "set_fields": [
            (("init", "A1 set parity", "--complexity", "Standard", "--issue-ref", "#506"), "2026-08-16T07:03:05Z"),
            (("set", "complexity=Complex", "custom_legacy_field=preserved"), "2026-08-16T07:03:05Z"),
        ],
        "terminal_rejection": [
            (("init", "A1 terminal rejection", "--complexity", "Standard"), "2026-08-16T08:04:06Z"),
            (("advance", "--phase", "done"), "2026-08-16T08:04:06Z"),
            (("advance", "--phase", "halted"), "2026-08-16T08:04:06Z"),
        ],
        "reactivate_without_approval": [
            (("init", "A1 approval rejection", "--complexity", "Standard"), "2026-08-16T08:05:07Z"),
            (("mark-halt", "--reason", "approval required", "--category", "awaiting-approval"), "2026-08-16T08:05:07Z"),
            (("reactivate", "--reason", "missing explicit approval", "--expected-category", "awaiting-approval"), "2026-08-16T08:05:07Z"),
        ],
        "missing_plan": [
            (("init", "A1 missing plan", "--complexity", "Standard"), "2026-08-16T09:06:08Z"),
            (("advance", "--phase", "executing"), "2026-08-16T09:06:08Z"),
        ],
        "set_narrowing": [
            (("init", "A1 set narrowing", "--complexity", "Standard"), "2026-08-16T09:07:09Z"),
        ],
    }
    for name, steps in simple_cases.items():
        cases[name] = _case(runs, cli, name, steps)

    update_root = (runs / "update_project_root").resolve()
    update_root.mkdir()
    destination = update_root / "moved-project"
    destination.mkdir()
    cases["update_project_root"] = {
        "steps": [
            _capture(cli, update_root, ("init", "A1 root parity", "--complexity", "Standard"), now="2026-08-16T06:02:04Z"),
            _capture(cli, update_root, ("update-project-root", "--path", str(destination)), now="2026-08-16T06:02:04Z"),
        ]
    }

    corrupt_root = (runs / "corrupt_aggregate").resolve()
    corrupt_root.mkdir()
    corrupt_steps = [
        _capture(cli, corrupt_root, ("init", "A1 aggregate fault", "--complexity", "Standard"), now="2026-08-16T09:05:07Z")
    ]
    (corrupt_root / ".mission-state" / "aggregate.json").write_text("{corrupt", encoding="utf-8")
    corrupt_steps.append(
        _capture(
            cli,
            corrupt_root,
            ("mark-halt", "--reason", "aggregate fault must not roll back authority", "--category", "blocked-external"),
            now="2026-08-16T09:05:07Z",
        )
    )
    cases["corrupt_aggregate"] = {"steps": corrupt_steps}
    return cases


def main() -> int:
    arguments = _parser().parse_args()
    legacy_cli = arguments.legacy_cli.resolve()
    if not legacy_cli.is_file():
        raise SystemExit(f"legacy CLI does not exist: {legacy_cli}")
    if len(arguments.source_revision) != 40:
        raise SystemExit("--source-revision must be a full 40-character commit id")

    with tempfile.TemporaryDirectory(prefix="mission-a1-golden-") as temporary:
        temporary_root = Path(temporary)
        legacy_root = temporary_root / "legacy-skill"
        (legacy_root / "bin").mkdir(parents=True)
        shutil.copytree(SKILL_ROOT / "lib", legacy_root / "lib")
        shutil.copy2(legacy_cli, legacy_root / "bin" / "mission-state.py")
        payload = {
            "schema": "mission-lifecycle-a1-golden/1",
            "provenance": {
                "source": "actual extraction-predecessor mission-state.py CLI output",
                "source_revision": arguments.source_revision,
                "generator": "python skills/mission/tests/fixtures/lifecycle_a1/generate.py --legacy-cli /path/to/extraction-predecessor/mission-state.py --source-revision <full-commit-id>",
                "instructions": "Materialize the recorded revision's skills/mission/bin/mission-state.py, pass it explicitly to the generator, inspect the diff, and never hand-edit case output.",
                "normalization": {
                    "project_root": ROOT_TOKEN,
                    "pid_fields": FIXED_PID,
                    "hostname": FIXED_HOSTNAME,
                    "generated_selection_id": "sel_<generated>",
                    "generated_assumptions_path": "<generated-assumptions-path>",
                },
            },
            "cases": _build_cases(temporary_root / "runs", legacy_root / "bin" / "mission-state.py"),
        }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
