"""Issue #626: thin adapter allowlist scanner and baseline ratchet."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-thin-adapter-ratchet.py"
BASELINE_PATH = (
    REPO_ROOT / "skills" / "mission" / "tests" / "fixtures" / "thin-adapter-baseline.jsonl"
)


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("thin_adapter_guard", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_composite_offender_reports_every_structural_rule():
    guard = _load_guard_module()
    source = '''
from datetime import datetime, timedelta

def cmd_offender(args, state):
    deadline = datetime.now() + timedelta(minutes=30)
    if state.get("phase") == "reviewing" and args.score >= 4.0:
        chosen = {item["id"]: item for item in state["reviews"] if item["open"]}
        for item in chosen.values():
            state.setdefault("accepted", []).append(item)
    print(deadline)
'''

    violations = guard.scan_source(source, path="fixture.py")
    actual_rules = {item.rule_id for item in violations}

    assert {
        "time.policy",
        "control.branch",
        "logic.compare",
        "logic.boolean",
        "logic.threshold-literal",
        "state.raw-access",
        "logic.comprehension",
        "control.loop",
        "state.mutation",
        "logic.business-container",
    } <= actual_rules


def test_typed_request_use_case_render_and_exit_is_allowlisted():
    guard = _load_guard_module()
    source = '''
import json
import sys
from pathlib import Path
from mission_application.example import ExampleRequest, run_example

def cmd_clean(args):
    request = ExampleRequest(
        value=str(args.value),
        path=Path(getattr(args, "path", "default.txt")),
    )
    result = run_example(request)
    print(json.dumps(result))
    sys.exit(0)
'''

    assert guard.scan_source(source, path="fixture.py") == []


def test_multiple_application_use_cases_cannot_be_composed_in_an_adapter():
    guard = _load_guard_module()
    source = '''
from mission_application.example import FirstRequest, SecondRequest, run_first, run_second

def cmd_composed(args):
    first = run_first(FirstRequest(value=args.value))
    second = run_second(SecondRequest(value=first))
    print(first, second)
'''

    rules = {item.rule_id for item in guard.scan_source(source, path="fixture.py")}

    assert "call.non-allowlisted" in rules


def test_try_fallback_is_rejected_but_named_failure_to_exit_mapping_is_allowed():
    guard = _load_guard_module()
    fallback_source = '''
from mission_application.example import ExampleFailure, ExampleRequest, run_fallback, run_primary

def cmd_fallback(args):
    request = ExampleRequest(value=args.value)
    try:
        result = run_primary(request)
    except ExampleFailure:
        result = run_fallback(request)
    print(result)
'''
    allowed_source = '''
import sys
from mission_application.example import ExampleFailure, ExampleRequest, run_primary

def cmd_allowed(args):
    request = ExampleRequest(value=args.value)
    try:
        result = run_primary(request)
    except ExampleFailure as error:
        sys.stderr.write(str(error))
        sys.exit(2)
    print(result)
'''

    fallback_rules = {
        item.rule_id
        for item in guard.scan_source(fallback_source, path="fixture.py")
    }

    assert "call.non-allowlisted" in fallback_rules
    assert guard.scan_source(allowed_source, path="fixture.py") == []


@pytest.mark.parametrize(
    "handler_body",
    [
        "pass",
        "print(str(error))",
        "raise",
    ],
)
def test_named_failure_mapping_requires_both_renderer_and_exit(handler_body):
    guard = _load_guard_module()
    source = f'''
from mission_application.example import ExampleFailure, ExampleRequest, run_primary

def cmd_incomplete(args):
    request = ExampleRequest(value=args.value)
    try:
        result = run_primary(request)
    except ExampleFailure as error:
        {handler_body}
    print(result)
'''

    rules = {item.rule_id for item in guard.scan_source(source, path="fixture.py")}

    assert "call.non-allowlisted" in rules


def test_module_qualified_request_and_single_use_case_are_not_miscounted():
    guard = _load_guard_module()
    source = '''
import mission_application.example as application

def cmd_clean(args):
    request = application.ExampleRequest(value=args.value)
    result = application.run_primary(request)
    print(result)
'''

    assert guard.scan_source(source, path="fixture.py") == []


def test_module_qualified_named_failure_mapping_is_allowlisted():
    guard = _load_guard_module()
    source = '''
import sys
import mission_application.example as application

def cmd_clean(args):
    request = application.ExampleRequest(value=args.value)
    try:
        result = application.run_primary(request)
    except application.ExampleFailure as error:
        sys.stderr.write(str(error))
        sys.exit(2)
    print(result)
'''

    assert guard.scan_source(source, path="fixture.py") == []


def test_transitive_local_helper_cannot_hide_a_violation():
    guard = _load_guard_module()
    source = '''
def _choose(value):
    return value if value > 3 else 3

def cmd_clean(args):
    print(_choose(args.value))
'''

    violations = guard.scan_source(source, path="fixture.py")

    assert {(item.function, item.rule_id) for item in violations} >= {
        ("_choose", "control.branch"),
        ("_choose", "logic.compare"),
        ("_choose", "logic.threshold-literal"),
    }


def test_parser_wiring_is_allowed_but_parser_helper_state_read_is_not():
    guard = _load_guard_module()
    clean_source = '''
import argparse

def cmd_clean(args):
    print(args.value)

def _build_parser():
    parser = argparse.ArgumentParser()
    command = parser.add_parser("clean")
    command.add_argument("--value", type=str)
    command.set_defaults(func=cmd_clean)
    return parser
'''
    dirty_source = '''
import argparse

def _parser_policy(state):
    return state.get("phase")

def cmd_clean(args):
    print(args.value)

def _build_parser():
    parser = argparse.ArgumentParser()
    command = parser.add_parser("clean")
    command.set_defaults(func=cmd_clean)
    _parser_policy({"phase": "ready"})
    return parser
'''

    assert guard.scan_source(clean_source, path="fixture.py") == []
    assert any(
        item.function == "_parser_policy" and item.rule_id == "state.raw-access"
        for item in guard.scan_source(dirty_source, path="fixture.py")
    )


def test_parser_wiring_does_not_exempt_dynamic_policy_branches():
    guard = _load_guard_module()
    source = '''
import argparse

def cmd_clean(args):
    print(args.value)

def _build_parser(state):
    parser = argparse.ArgumentParser()
    if state.get("enabled"):
        parser.add_parser("clean").set_defaults(func=cmd_clean)
    return parser
'''

    rules = {item.rule_id for item in guard.scan_source(source, path="fixture.py")}

    assert {"control.branch", "state.raw-access"} <= rules


@pytest.mark.parametrize(
    ("rule_id", "statement"),
    [
        ("control.branch", "if args.value:\n        print(args.value)"),
        ("control.loop", "for item in args.values:\n        print(item)"),
        ("logic.compare", "print(args.value == 'ready')"),
        ("logic.boolean", "print(args.left and args.right)"),
        ("logic.arithmetic", "print(args.left + args.right)"),
        ("logic.comprehension", "print([item for item in args.values])"),
        ("logic.threshold-literal", "print(1)"),
        ("state.raw-access", "print(state.get('phase'))"),
        ("state.mutation", "state['phase'] = args.value"),
        ("logic.business-container", "print({'phase': args.value})"),
        ("time.policy", "print(datetime.now())"),
        ("io.direct", "print(Path(args.path).read_text())"),
        ("call.non-allowlisted", "print(external_policy(args.value))"),
        ("dispatch.dynamic", "getattr(args.module, args.name)()"),
    ],
)
def test_each_rule_has_a_positive_and_mutation_fixture(rule_id, statement):
    guard = _load_guard_module()
    imports = "from datetime import datetime\nfrom pathlib import Path\n"
    offender = imports + "\ndef cmd_offender(args, state):\n    " + statement + "\n"
    mutation = imports + "\ndef cmd_offender(args, state):\n    print(args.value)\n"

    offender_rules = {item.rule_id for item in guard.scan_source(offender, path="fixture.py")}
    mutation_rules = {item.rule_id for item in guard.scan_source(mutation, path="fixture.py")}

    assert rule_id in offender_rules
    assert rule_id not in mutation_rules


@pytest.mark.parametrize(
    "text",
    [
        '{"path":"a.py","function":"cmd_a","rules":{"control.branch":1}}\n'
        '{"path":"a.py","function":"cmd_a","rules":{"logic.compare":1}}\n',
        '{"path":"a.py","function":"cmd_a","rules":{"unknown":1}}\n',
        '{"path":"a.py","function":"cmd_a","rules":{"control.branch":-1}}\n',
        '{"path":"a.py","function":"cmd_a","rules":{"control.branch":true}}\n',
        '{"path":"a.py","function":"cmd_a","rules":{"control.branch":1},"extra":1}\n',
    ],
)
def test_baseline_schema_rejects_noncanonical_or_unsafe_records(text):
    guard = _load_guard_module()

    with pytest.raises(guard.BaselineError):
        guard.load_baseline_text(text)


def test_baseline_round_trip_uses_function_rule_count_identity_only():
    guard = _load_guard_module()
    violations = [
        guard.Violation("adapter.py", "cmd_a", "logic.compare", 91),
        guard.Violation("adapter.py", "cmd_a", "logic.compare", 127),
        guard.Violation("adapter.py", "cmd_a", "control.branch", 90),
    ]

    baseline = guard.baseline_from_violations(violations)
    rendered = guard.dump_baseline(baseline)

    assert rendered == (
        '{"path":"adapter.py","function":"cmd_a","rules":'
        '{"control.branch":1,"logic.compare":2}}\n'
    )
    assert guard.load_baseline_text(rendered) == baseline


@pytest.mark.parametrize(
    ("current", "expected_fragment"),
    [
        ({("adapter.py", "cmd_a"): {"logic.compare": 3}}, "increased"),
        (
            {("adapter.py", "cmd_a"): {"logic.compare": 2, "control.branch": 1}},
            "new rule",
        ),
        ({("adapter.py", "cmd_b"): {"logic.compare": 2}}, "new function"),
    ],
)
def test_ratchet_rejects_count_rule_or_function_growth(current, expected_fragment):
    guard = _load_guard_module()
    base = {("adapter.py", "cmd_a"): {"logic.compare": 2}}

    errors = guard.compare_baselines(base, current)

    assert any(expected_fragment in error for error in errors)


@pytest.mark.parametrize(
    "current",
    [
        {},
        {("adapter.py", "cmd_a"): {"logic.compare": 1}},
        {("adapter.py", "cmd_a"): {"logic.compare": 2}},
    ],
)
def test_ratchet_allows_only_same_or_decreasing_baselines(current):
    guard = _load_guard_module()
    base = {("adapter.py", "cmd_a"): {"logic.compare": 2}}

    assert guard.compare_baselines(base, current) == []


def test_repository_scan_matches_the_headroom_free_baseline_exactly():
    guard = _load_guard_module()

    scanned = guard.scan_repository(REPO_ROOT)
    recorded = guard.load_baseline_text(BASELINE_PATH.read_text(encoding="utf-8"))

    assert scanned == recorded
    assert guard.dump_baseline(recorded) == BASELINE_PATH.read_text(encoding="utf-8")
    assert ("skills/mission/bin/mission-state.py", "_derive_next_action") not in recorded


def test_current_scan_rejects_new_strict_code_and_stale_headroom():
    guard = _load_guard_module()
    new_handler = guard.baseline_from_violations(
        guard.scan_source(
            "def cmd_new(args):\n    if args.enabled:\n        print(args.enabled)\n",
            path="adapter.py",
        )
    )
    stale = {("adapter.py", "cmd_old"): {"control.branch": 2}}
    reduced = {("adapter.py", "cmd_old"): {"control.branch": 1}}

    assert any(
        "baseline missing function" in error
        for error in guard._baseline_mismatch_errors({}, new_handler)
    )
    assert any(
        "count mismatch" in error
        for error in guard._baseline_mismatch_errors(stale, reduced)
    )


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_git_show_reads_a_synthetic_commit_without_using_the_worktree(tmp_path):
    guard = _load_guard_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Mission Test")
    _git(repo, "config", "user.email", "mission-test@example.invalid")
    tracked = repo / "nested" / "baseline.jsonl"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("recorded\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "synthetic baseline")
    commit = _git(repo, "rev-parse", "HEAD")
    tracked.write_text("dirty worktree\n", encoding="utf-8")

    assert guard._git_show(repo, commit, Path("nested/baseline.jsonl")) == "recorded\n"
    assert guard._git_show(repo, commit, Path("missing.jsonl")) is None


def test_load_base_baseline_uses_recorded_or_bootstrap_source_commit(tmp_path):
    guard = _load_guard_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Mission Test")
    _git(repo, "config", "user.email", "mission-test@example.invalid")
    source = (
        "def cmd_bootstrap(args):\n"
        "    if args.enabled:\n"
        "        print(args.enabled)\n"
    )
    source_path = repo / guard.SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "synthetic source")
    source_commit = _git(repo, "rev-parse", "HEAD")

    bootstrap, bootstrap_kind = guard.load_base_baseline(repo, source_commit)

    assert bootstrap_kind == "bootstrap-source-scan"
    assert bootstrap == guard.baseline_from_violations(
        guard.scan_source(source, path=guard.SOURCE_PATH.as_posix())
    )

    baseline = {
        (guard.SOURCE_PATH.as_posix(), "cmd_bootstrap"): {"control.branch": 1}
    }
    baseline_path = repo / guard.BASELINE_PATH
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(guard.dump_baseline(baseline), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "synthetic recorded baseline")
    baseline_commit = _git(repo, "rev-parse", "HEAD")

    recorded, recorded_kind = guard.load_base_baseline(repo, baseline_commit)

    assert recorded_kind == "recorded-baseline"
    assert recorded == baseline
    with pytest.raises(guard.BaselineError, match="base SHA"):
        guard.load_base_baseline(repo, "not-a-sha")
