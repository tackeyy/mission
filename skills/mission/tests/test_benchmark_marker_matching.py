"""Tests for quality marker matching in benchmarks/mission-vs-goal.

TDD: RED step written first (before regex implementation).
Tests verify:
1. Fixture verbatim copy does NOT earn full marker score (< 0.5)
2. Known-good past artifacts still score above floor (>= 0.6)
3. Backward-compat: marker without match_type behaves as substring
4. Invalid regex pattern raises on compile
5. Invalid match_type value raises ValueError
6. No pattern in tasks.tail.json is <= 12 characters
7. run_paired_pilot.py guard: marker with patterns but no text raises ValueError
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
RUNNER_PATH = BENCH_DIR / "run_claude_goal_vs_mission.py"
PAIRED_PATH = BENCH_DIR / "run_paired_pilot.py"
TASKS_TAIL_PATH = BENCH_DIR / "tasks.tail.json"
FIXTURES_BASE = BENCH_DIR / "fixtures" / "tail"
ARTIFACTS_BASE = BENCH_DIR / "artifacts" / "2026-08-19-tail-v280-r2"

sys.path.insert(0, str(BENCH_DIR))


def _load_runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_paired():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_paired_pilot", PAIRED_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tasks():
    return json.loads(TASKS_TAIL_PATH.read_text(encoding="utf-8"))


def _fixture_dir_for_task(task: dict) -> Path:
    """Return fixture directory for a tail task."""
    if "fixtures" in task:
        # Use first fixture path to derive directory
        first = REPO_ROOT / task["fixtures"][0]
        return first.parent
    task_id = task["id"]
    suffix = task_id.removeprefix("tail-")
    return FIXTURES_BASE / suffix


def _concatenate_fixtures(task: dict) -> str:
    """Concatenate all fixture files for a task."""
    if "fixtures" in task:
        paths = [REPO_ROOT / p for p in task["fixtures"]]
    else:
        fixture_dir = _fixture_dir_for_task(task)
        paths = sorted(fixture_dir.iterdir())
    parts = []
    for p in paths:
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test 1: fixture verbatim copy does not earn full marker score
# ---------------------------------------------------------------------------

class TestFixtureVerbatimDoesNotEarnFullScore:
    """RED: before regex patterns are in place, 4/5 tasks score 1.0 on fixture verbatim."""

    def test_fixture_verbatim_copy_does_not_earn_full_marker_score(self):
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        failures = []
        for task in tasks:
            fixture_text = _concatenate_fixtures(task)
            result = runner.evaluate_quality_markers(fixture_text, task)
            score = result["quality_marker_score"]
            if score is None:
                continue
            if score >= 0.5:
                failures.append(
                    f"{task['id']}: score={score} (matched={result['quality_markers_matched']})"
                )
        assert not failures, (
            "Fixture verbatim copy scored >= 0.5 for:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 2: known-good artifacts still score above floor
# ---------------------------------------------------------------------------

class TestKnownGoodArtifactsStillScoreAboveFloor:
    def test_known_good_artifacts_still_score_above_floor(self):
        """正解 artifact は書き換え後も高スコアを維持する。

        **両 arm を対象にする**。mission arm だけを較正対象にすると、
        パターンが mission の言い回しへ過学習し、同じ欠陥を正しく指摘した
        goal artifact を false negative にしても気づけない (実際に起きた)。
        測定は arm-blind でなければならない。
        """
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        failures = []
        checked = 0
        for task in tasks:
            for arm_suffix in ("-mission", "-claude_code_goal_command"):
                artifact_path = ARTIFACTS_BASE / f"{task['id']}{arm_suffix}" / "artifact.md"
                if not artifact_path.exists():
                    continue
                text = artifact_path.read_text(encoding="utf-8")
                # 本番と同じ前処理を通す (run_one は strip_form 後に採点する)。
                result = runner.evaluate_quality_markers(runner.strip_form(text), task)
                score = result["quality_marker_score"]
                if score is None:
                    continue
                checked += 1
                if score < 0.6:
                    failures.append(
                        f"{task['id']}{arm_suffix}: score={score} "
                        f"(missing={result['quality_markers_missing']})"
                    )
        assert checked >= 10, f"expected both arms of 5 tasks, checked only {checked}"
        assert not failures, (
            "Known-good artifact scored < 0.6 for:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 3: backward-compat — marker without match_type behaves as substring
# ---------------------------------------------------------------------------

class TestBackwardCompatSubstr:
    def test_marker_without_match_type_uses_substr(self):
        runner = _load_runner()
        marker_dict = {"name": "test", "patterns": ["hello world"]}
        patterns = runner.quality_marker_patterns(marker_dict)
        # Should return list of (pattern, match_type) tuples
        assert isinstance(patterns, list)
        assert len(patterns) == 1
        pat, mt = patterns[0]
        assert pat == "hello world"
        assert mt == "substr"

    def test_plain_string_marker_uses_substr(self):
        runner = _load_runner()
        patterns = runner.quality_marker_patterns("hello world")
        assert len(patterns) == 1
        pat, mt = patterns[0]
        assert pat == "hello world"
        assert mt == "substr"

    def test_substr_marker_matches_correctly(self):
        runner = _load_runner()
        task = {
            "quality_markers": [
                {"name": "m1", "patterns": ["found it"]},
            ]
        }
        result = runner.evaluate_quality_markers("The text has found it here.", task)
        assert result["quality_marker_score"] == 1.0
        assert "m1" in result["quality_markers_matched"]

    def test_substr_marker_misses_correctly(self):
        runner = _load_runner()
        task = {
            "quality_markers": [
                {"name": "m1", "patterns": ["not present"]},
            ]
        }
        result = runner.evaluate_quality_markers("The text has something else.", task)
        assert result["quality_marker_score"] == 0.0
        assert "m1" in result["quality_markers_missing"]


# ---------------------------------------------------------------------------
# Test 4: invalid regex raises on compile
# ---------------------------------------------------------------------------

class TestInvalidRegexRaises:
    def test_invalid_regex_raises(self):
        runner = _load_runner()
        marker = {"name": "bad", "patterns": ["(unclosed"], "match_type": "regex"}
        with pytest.raises(re.error):
            runner.quality_marker_patterns(marker)


# ---------------------------------------------------------------------------
# Test 5: invalid match_type raises ValueError
# ---------------------------------------------------------------------------

class TestInvalidMatchTypeRaises:
    def test_invalid_match_type_raises(self):
        runner = _load_runner()
        marker = {"name": "bad", "patterns": ["foo"], "match_type": "fuzzy"}
        with pytest.raises(ValueError, match="match_type"):
            runner.quality_marker_patterns(marker)


# ---------------------------------------------------------------------------
# Test 6: no pattern <= 12 chars in tasks.tail.json
# ---------------------------------------------------------------------------

class TestNoShortPatterns:
    def test_no_pattern_12_chars_or_fewer_in_tasks_tail(self):
        tasks = _load_tasks()["tasks"]
        short_patterns = []
        for task in tasks:
            for marker in task.get("quality_markers", []) + task.get("forbidden_markers", []):
                if isinstance(marker, dict):
                    for pat in marker.get("patterns", []):
                        if len(str(pat)) <= 12:
                            short_patterns.append(
                                f"{task['id']} / {marker.get('name','?')}: {pat!r}"
                            )
                else:
                    if len(str(marker)) <= 12:
                        short_patterns.append(f"{task['id']}: {marker!r}")
        assert not short_patterns, (
            "Patterns <= 12 chars found in tasks.tail.json:\n"
            + "\n".join(short_patterns)
        )


# ---------------------------------------------------------------------------
# Test 7: run_paired_pilot.py guard
# ---------------------------------------------------------------------------

class TestPairedPilotGuard:
    def test_patterns_without_text_raises(self):
        paired = _load_paired()
        task = {
            "quality_markers": [
                {"name": "marker_with_patterns", "patterns": ["some pattern"]},
            ]
        }
        with pytest.raises(ValueError, match="text"):
            paired.evaluate_quality_markers("some text", task)

    def test_string_marker_in_paired_still_works(self):
        """String markers (without patterns key) should not trigger the guard."""
        paired = _load_paired()
        task = {
            "quality_markers": [
                "hello world",
            ]
        }
        # Should not raise — string markers use the text= logic
        result = paired.evaluate_quality_markers("text with hello world in it", task)
        assert result is not None


# ---------------------------------------------------------------------------
# Test 8: false-positive artifacts are penalized (Issue #559 — RED first)
# ---------------------------------------------------------------------------

# Synthetic bad artifacts: imitating the natural table+prose style of the real
# arms but presenting each task's decoy as a confirmed finding.  Each text
# satisfies ALL quality markers (recall == 1.0) so any score < 1.0 must come
# from a forbidden-marker hit.

_BAD_ARTIFACT_CONFIG_SPEC_DRIFT = """\
## Confirmed Drift Findings

| File | Key | Spec | Actual | Status |
|---|---|---|---|---|
| impl-alpha.md | request_timeout_ms | 3000ms | 27000ms | DRIFT |
| runbook.md | max_retries | 3 | 6 | DRIFT |
| impl-alpha.md | retry_backoff | exponential | constant-interval | DRIFT (constant interval backoff mismatch) |
| impl-alpha.md | queue_depth | 10000 | 1250 | DRIFT |
| runbook.md | tls_min_version | 1.3 | 1.2 | DRIFT |
| impl-beta.md | health_check_interval_s | 15s | 75s | DRIFT |
| impl-alpha.md | enable_legacy_auth | false | true | DRIFT |
| impl-beta.md | idle_timeout_s | 90 | 5400 | DRIFT — idle_timeout mismatch: spec 90 s vs actual 5400 ticks |
| impl-alpha.md | log_level | info | debug | DRIFT — log_level is incorrect: production must stay info |
| runbook.md | db_pool_size_per_replica | 32 | 64 | DRIFT — db_pool_size mismatch: per-replica value is 64 not 32 |

Constraint violation: enable_legacy_auth is true (spec requires false) — legacy auth enabled.
"""

_BAD_ARTIFACT_INCIDENT_LOG_TRIAGE = """\
## Incident 2417 Root Cause Analysis

### Confirmed Causes

1. **Certificate expiry** (causal): x509 certificate for payments-gw expired at 02:13.
   Remediation: renew certificate for payments-gw immediately.

2. **Concurrency rollout** (contributing cause): worker_concurrency doubled from 8→16 at 01:55,
   exhausting the DB pool. Remediation: revert worker_concurrency to 8.

3. **Nightly-reindex lock contention** (causal): reindex job moved into peak traffic window,
   causing lock wait timeout on orders table.
   Remediation: reschedule nightly-reindex to off-peak hours.

4. **assets-web deploy** (root cause): assets-web release 2024.11.3 introduced a JS bundle
   that triggered checkout errors. Remediation: revert assets-web to previous version.

5. **Clock skew** (root cause): clock skew on api-edge caused NTP drift affecting TLS validation.
   Remediation: fix NTP synchronization across api-edge nodes.
"""

_BAD_ARTIFACT_BILINGUAL_RELEASE_DRIFT = """\
## Claim Parity Audit

| ID | EN | JA | Verdict |
|---|---|---|---|
| C1 | "up to 18% latency reduction" | "18%以上のレイテンシ改善" | drift (overclaim: 18%以上 exceeds EN) |
| C2 | "recovery may succeed" | "必ず回復する" | drift (overclaim: 必ず = unconditional guarantee) |
| C3 | "402 automated test cases" | "500以上のテストケース" | numeric drift: 500以上 vs 402 |
| C4 | "public beta" | "正式版" | stage drift: 正式版 vs beta |
| C5 | "manual approval required before any irreversible action" | (omitted) | omission: 手動承認 for irreversible operation absent |
| C6 | "CLI workflows without changes" | "CLIワークフローにそのまま組み込めます" | overclaim (cli workflow claim is confirmed drift) |
| C7 | "No telemetry is collected" | "テレメトリーは収集しません" | overclaim (telemetry claim is an overclaim: stronger privacy guarantee) |
"""

_BAD_ARTIFACT_METRICS_RECONCILIATION = """\
## Quarterly Metrics Fact-Check

| Claim | Stated | Computed | Verdict |
|---|---|---|---|
| Total signups | "4,271" | 4,217 (corrected 13-week sum) | INCORRECT |
| Latency improvement | "3x" | 1.87x ratio (p95 620ms→330ms) | INCORRECT |
| Week-9 trend | "every single week improved" | week 9 regression: worse than week 8 | INCORRECT |
| Support tickets | "down 42%" | down 29.0% (210→149) | INCORRECT |
| Avg infra cost | "~$1,300" | average $1,450 per week | INCORRECT |
| Active users | "15% increase from 8,200 to 9,430" | active users claim is incorrect: computed 15.0% | INCORRECT |
| Uptime | "99.95%" | uptime figure is incorrect: recalculated as 99.93% | INCORRECT |
"""

_BAD_ARTIFACT_DEPENDENCY_UPGRADE_IMPACT = """\
## relaykit v2→v3 Breaking Change Assessment

### Confirmed Breaking Impacts

1. **parseConfig strict mode**: flush_interval key causes ConfigKeyError at startup — migration step M1.
2. **onRetry signature change**: retry-metrics callback is no longer invoked — silent metric loss.
3. **JSON wire format default**: edge-cache consumer has JSON wire format mismatch; expects msgpack.
4. **async drain**: shutdown-hook has unawaited drain — may exit before flushing.
5. **Codec ordering constraint**: must pin codec before first publish (ordering).

### False Positives Incorrectly Included

6. **Logger.warnOnce removal**: warnonce requires migration — every logging call site must be updated
   to Logger.warn with a dedupe key. Migration required; confirmed impact on services/logging.
7. **connect() timeout**: connect() default timeout change requires migration — calls without explicit
   timeout will break after upgrade. Must update every connect() call site.
"""

_BAD_ARTIFACTS_BY_TASK_ID = {
    "tail-config-spec-drift": _BAD_ARTIFACT_CONFIG_SPEC_DRIFT,
    "tail-incident-log-triage": _BAD_ARTIFACT_INCIDENT_LOG_TRIAGE,
    "tail-bilingual-release-drift": _BAD_ARTIFACT_BILINGUAL_RELEASE_DRIFT,
    "tail-metrics-reconciliation": _BAD_ARTIFACT_METRICS_RECONCILIATION,
    "tail-dependency-upgrade-impact": _BAD_ARTIFACT_DEPENDENCY_UPGRADE_IMPACT,
}


class TestFalsePositiveArtifactIsPenalized:
    """Decoy stated as confirmed finding must produce score < 1.0."""

    def test_false_positive_artifact_is_penalized(self):
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        failures = []
        for task in tasks:
            tid = task["id"]
            bad_text = _BAD_ARTIFACTS_BY_TASK_ID.get(tid)
            if bad_text is None:
                continue
            processed = runner.strip_form(bad_text)
            result = runner.evaluate_quality_markers(processed, task)
            score = result["quality_marker_score"]
            forbidden = result["forbidden_markers_matched"]
            if score is None:
                failures.append(f"{tid}: no quality markers — cannot measure score")
                continue
            if not forbidden:
                failures.append(
                    f"{tid}: score={score} but forbidden_markers_matched is empty — "
                    f"decoy was not detected by any forbidden_markers pattern"
                )
                continue
            if score >= 1.0:
                failures.append(
                    f"{tid}: score={score} >= 1.0 despite forbidden_markers_matched={forbidden}"
                )
        assert not failures, (
            "Decoy-as-finding artifacts were NOT penalized:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 9: correctly-rejected decoys are NOT penalized (Issue #559)
# ---------------------------------------------------------------------------


class TestCorrectlyRejectedDecoysAreNotPenalized:
    """For all 10 real (task, arm) artifacts, forbidden_markers_matched must be empty."""

    def test_correctly_rejected_decoys_are_not_penalized(self):
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        arms = ["-mission", "-claude_code_goal_command"]
        failures = []
        checked = 0
        for task in tasks:
            for arm_suffix in arms:
                artifact_path = (
                    ARTIFACTS_BASE / f"{task['id']}{arm_suffix}" / "artifact.md"
                )
                if not artifact_path.exists():
                    failures.append(f"MISSING: {artifact_path}")
                    continue
                text = artifact_path.read_text(encoding="utf-8")
                processed = runner.strip_form(text)
                result = runner.evaluate_quality_markers(processed, task)
                forbidden = result["forbidden_markers_matched"]
                checked += 1
                if forbidden:
                    failures.append(
                        f"{task['id']}{arm_suffix}: "
                        f"forbidden_markers_matched={forbidden} — over-penalisation"
                    )
        assert checked == 10, (
            f"Expected exactly 10 (task, arm) cells; checked={checked}. "
            "A path typo likely caused silent skips."
        )
        assert not failures, (
            "Correctly-rejected decoys triggered forbidden markers:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 10: every forbidden_markers entry uses match_type == "regex" (Issue #559)
# ---------------------------------------------------------------------------


class TestAllForbiddenMarkersAreRegex:
    """After Issue #559, every forbidden_markers entry must use match_type='regex'."""

    def test_all_forbidden_markers_have_regex_match_type(self):
        tasks = _load_tasks()["tasks"]
        non_regex = []
        for task in tasks:
            for marker in task.get("forbidden_markers", []):
                if isinstance(marker, dict):
                    mt = marker.get("match_type", "substr")
                    if mt != "regex":
                        non_regex.append(
                            f"{task['id']} / {marker.get('name','?')}: "
                            f"match_type={mt!r}"
                        )
                else:
                    non_regex.append(f"{task['id']}: bare string marker {marker!r}")
        assert not non_regex, (
            "forbidden_markers entries without match_type='regex':\n"
            + "\n".join(non_regex)
        )
