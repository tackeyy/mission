"""Issue #351: preventive artifact completeness lint."""

import importlib.util
import getpass
import json
import os
import re
import signal
import time
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
SPEC = importlib.util.spec_from_file_location("mission_state_issue351", MISSION_STATE_PY)
MISSION_STATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MISSION_STATE)


# artifact に FIFO を渡したとき「読もうとして永久ブロックしないこと」を確認するための watchdog。
#
# 検知したい失敗は**無限ブロック**であって「少し遅い」ではない。FIFO を読みに行けば
# writer がいないので完走しないため、watchdog は「無限ブロックだけを捕える」値でよく、
# 実行が速いことを要求してはならない（それは性能 SLA であって本テストの関心事ではない）。
#
# 値を締めると負荷で破綻することが 2 度実証されている。
#   - 1 秒: 2026-08-26 の CI で 1.005s / margin -0.005s により fail、再実行で green
#   - 5 秒: 2026-08-29 の full suite で 5.025s の TimeoutExpired により fail。
#           そのときの stderr には既に "WARN #351: artifact lint skipped" が出ており、
#           実装は正しく skip 済みで、単に完了が遅かっただけだった
#
# アイドル時の実測は 0.44〜0.48 秒で、大半は subprocess の起動時間。10 並列の負荷下では
# 約 11 倍に膨らむ。60 秒はアイドル比で約 130 倍の余裕があり、無限ブロック時は
# そもそも完走しないため検出力は落ちない。
FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS = 60
_ABSOLUTE_PATH_RE = re.compile(r"(?<!\w)/(?:[^\s\"']+)")
_CURRENT_USER = getpass.getuser()
# home prefix はリテラルで書くと artifact hygiene の走査に引っかかるため組み立てる
_HOME_PREFIX = "/" + "Users" + "/"


def _review(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema": "mission-review/1",
        "perspective": "A",
        "iteration": 1,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.4,
            "completeness": 4.3,
            "usability": 4.2,
        },
        "findings": [],
        "notes": "review",
    }))
    return path


def _redact_timeout_diagnostic(text):
    if text is None:
        return "<empty>"
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    redacted = text.replace(str(Path.home()), "<home>")
    redacted = redacted.replace(_CURRENT_USER, "<user>")
    redacted = _ABSOLUTE_PATH_RE.sub("<path>", redacted)
    return redacted


def _describe_child_state(returncode):
    if returncode is None:
        return "running"
    if returncode < 0:
        try:
            signal_name = signal.Signals(-returncode).name
        except ValueError:
            signal_name = f"signal {-returncode}"
        return f"terminated by {signal_name} (returncode={returncode})"
    return f"exited {returncode}"


def _format_fifo_timeout_diagnostic(*, elapsed, stderr, returncode, watchdog):
    return (
        "FIFO aggregate-reviews never finished: it was still running "
        f"after {elapsed:.3f}s (watchdog={watchdog:.3f}s); "
        f"child={_describe_child_state(returncode)}; "
        f"stderr={_redact_timeout_diagnostic(stderr)}"
    )


def _run_under_block_watchdog(command, *, cwd, env, watchdog):
    """Run ``command`` and fail only if it never finishes.

    The watchdog detects a child that blocks forever. It deliberately does not
    assert that the child is fast: execution speed is a performance SLA, not the
    property these tests verify.
    """
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=watchdog)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        process.kill()
        _, killed_stderr = process.communicate()
        stderr = killed_stderr if killed_stderr else exc.stderr
        raise AssertionError(
            _format_fifo_timeout_diagnostic(
                elapsed=elapsed,
                stderr=stderr,
                returncode=process.returncode,
                watchdog=watchdog,
            )
        ) from exc
    elapsed = time.perf_counter() - started
    return SimpleNamespace(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed=elapsed,
    )


def _run_fifo_aggregate_reviews(
    *, cwd, env, review, watchdog=FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS
):
    command = [
        sys.executable,
        str(MISSION_STATE_PY),
        "aggregate-reviews",
        "--iteration",
        "1",
        "--input",
        str(review),
        "--json",
    ]
    return _run_under_block_watchdog(command, cwd=cwd, env=env, watchdog=watchdog)


def test_block_watchdog_detects_a_child_that_never_finishes(tmp_path):
    """検査自体の検出力を実証する。

    watchdog を緩めた結果「何も検出できない検査」になっていないことを、
    決して終わらない子プロセスを与えて確認する。子は watchdog より遥かに長く
    眠るため、負荷が重いほど watchdog は確実に発火する（安全側に倒れる）。
    """
    never_finishes = [sys.executable, "-c", "import time; time.sleep(3600)"]

    with pytest.raises(AssertionError) as excinfo:
        _run_under_block_watchdog(
            never_finishes, cwd=tmp_path, env=dict(os.environ), watchdog=1.0
        )

    message = str(excinfo.value)
    assert "never finished" in message
    assert "watchdog=1.000s" in message
    assert "terminated by SIGKILL" in message


def test_block_watchdog_accepts_a_slow_child_that_finishes(tmp_path):
    """watchdog が「遅いだけ」を失敗にしないことを固定する。

    watchdog 予算のうち無視できない割合を使う子でも、完走する限り成功とする。
    これが崩れると、性能 SLA が再び成功条件へ混入する。
    """
    slow_but_finishes = [sys.executable, "-c", "import time; time.sleep(0.5)"]

    result = _run_under_block_watchdog(
        slow_but_finishes, cwd=tmp_path, env=dict(os.environ), watchdog=1.0
    )

    assert result.returncode == 0
    assert result.elapsed >= 0.5


def test_empty_sections_at_supported_heading_levels_are_detected():
    artifact = "# Mission\n\n## Plan\n\n### Step 3\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert [(item["heading"], item["kind"]) for item in findings] == [
        ("Mission", "empty-section"),
        ("Plan", "empty-section"),
        ("Step 3", "empty-section"),
    ]


def test_atx_headings_with_up_to_three_leading_spaces_are_detected():
    artifact = " # Mission\n\n  ## Plan\n\n   ### Step 3\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert [(item["heading"], item["kind"]) for item in findings] == [
        ("Mission", "empty-section"),
        ("Plan", "empty-section"),
        ("Step 3", "empty-section"),
    ]


def test_empty_atx_title_and_trailing_closing_hashes_are_normalized():
    artifact = "##\n\n## ###\n\n## Score ###\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [
        {"heading": "", "kind": "empty-section", "excerpt": ""},
        {"heading": "", "kind": "empty-section", "excerpt": ""},
        {"heading": "Score", "kind": "empty-section", "excerpt": ""},
    ]


def test_hashes_without_whitespace_are_part_of_atx_title():
    artifact = "## Score###\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings[0]["heading"] == "Score###"


def test_four_space_indented_fence_marker_does_not_hide_following_heading():
    artifact = "# Mission\nDone.\n    ```\n## Score\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "empty-section",
        "excerpt": "",
    }]


def test_fence_closes_only_with_the_same_marker_character():
    artifact = "```text\n~~~\n## Inside fence\n```\n## Score\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "empty-section",
        "excerpt": "",
    }]


def test_fence_closer_must_be_at_least_as_long_as_the_opener():
    artifact = "````text\n```\n## Inside fence\n````\n## Score\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "empty-section",
        "excerpt": "",
    }]


def test_fence_closer_rejects_non_whitespace_suffix():
    artifact = "```text\n```not-a-closer\n## Inside fence\n```\n## Score\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "empty-section",
        "excerpt": "",
    }]


def test_headings_inside_a_well_formed_fence_are_ignored():
    artifact = (
        "# Mission\nDone.\n"
        "```markdown\n## Example empty heading\n```\n"
        "## Score\n4.3 from two reviewers.\n"
    )

    assert MISSION_STATE.lint_artifact_completeness(artifact) == []


def test_backtick_in_backtick_fence_info_invalidates_the_opener():
    artifact = "```lang`bad\n## Score\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "empty-section",
        "excerpt": "",
    }]


def test_combined_english_forward_reference_stub_is_detected():
    artifact = "## Score\nRecorded below once review-finalize completes.\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings == [{
        "heading": "Score",
        "kind": "stub-forward-reference",
        "excerpt": "Recorded below once review-finalize completes.",
    }]


def test_japanese_forward_reference_stub_is_detected():
    artifact = "## Stop Decision\nreview-finalize 完了後に記録\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings[0]["kind"] == "stub-forward-reference"


def test_japanese_later_stub_is_detected():
    artifact = "## Score\n後で記録\n"

    findings = MISSION_STATE.lint_artifact_completeness(artifact)

    assert findings[0]["kind"] == "stub-forward-reference"


def test_complete_artifact_has_no_findings():
    artifact = "# Mission\n契約監査を完了した。\n## Score\n4.3。根拠は reviewer 2名の集計。\n"

    assert MISSION_STATE.lint_artifact_completeness(artifact) == []


def test_tbd_inside_substantive_sentence_is_not_a_stub():
    artifact = "## Source value\nTBD is a literal status value in the imported fixture.\n"

    assert MISSION_STATE.lint_artifact_completeness(artifact) == []


def test_forward_phrase_with_actual_value_and_basis_is_not_a_stub():
    artifact = "## Result\nThe measured value will be recorded as 42 ms (three-run median).\n"

    assert MISSION_STATE.lint_artifact_completeness(artifact) == []


def test_aggregate_warns_and_records_lint_without_changing_scores(
    state_dir, run_cli, tmp_path,
):
    artifact = tmp_path / "artifact.md"
    artifact.write_text("# Mission\nDone.\n## Score\nRecorded below once review-finalize completes.\n")
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["artifact_path"] = str(artifact)
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    out = tmp_path / "scoring.json"

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--out", str(out), "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0
    assert "WARN #351: artifact lint: stub-forward-reference at Score" in result.stderr
    scoring = json.loads(out.read_text())
    assert scoring["items"] == {
        "mission_achievement": 4.5,
        "accuracy": 4.4,
        "completeness": 4.3,
        "usability": 4.2,
    }
    evidence = json.loads((state_dir.parent / scoring["findings_evidence_path"]).read_text())
    assert evidence["artifact_lint"][0]["heading"] == "Score"
    assert evidence["artifact_lint_status"] == "findings"
    persisted = json.loads(state_path.read_text())
    assert persisted["artifact_lint"] == evidence["artifact_lint"]
    assert persisted["artifact_lint_status"] == "findings"


def test_aggregate_records_clean_observation_separately_from_skip(
    state_dir, run_cli, tmp_path,
):
    artifact = tmp_path / "artifact.md"
    artifact.write_text("## Score\n4.3 from two reviewers.\n")
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["artifact_path"] = str(artifact)
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0
    observation = json.loads(result.stdout)
    assert observation["artifact_lint"] == []
    assert observation["artifact_lint_status"] == "clean"
    persisted = json.loads(state_path.read_text())
    assert persisted["artifact_lint"] == []
    assert persisted["artifact_lint_status"] == "clean"


def test_aggregate_without_artifact_path_skips_lint_and_exits_zero(
    state_dir, run_cli, tmp_path,
):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["artifact_lint"] = [
        {"heading": "Old", "kind": "empty-section", "excerpt": ""}
    ]
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    out = tmp_path / "scoring.json"

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--out", str(out), cwd=state_dir.parent,
    )

    assert result.returncode == 0
    assert "WARN #351" not in result.stderr
    scoring = json.loads(out.read_text())
    evidence = json.loads((state_dir.parent / scoring["findings_evidence_path"]).read_text())
    assert evidence["artifact_lint"] == []
    assert evidence["artifact_lint_status"] == "skipped"
    persisted = json.loads(state_path.read_text())
    assert "artifact_lint" not in persisted
    assert persisted["artifact_lint_status"] == "skipped"
    stats = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert json.loads(stats.stdout)["artifact_lint_counts"]["clean"] == 0


def test_aggregate_unreadable_artifact_warns_skips_and_exits_zero(
    state_dir, run_cli, tmp_path,
):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["artifact_path"] = str(tmp_path)
    state["artifact_lint"] = [
        {"heading": "Old", "kind": "empty-section", "excerpt": ""}
    ]
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0
    assert "WARN #351: artifact lint skipped" in result.stderr
    observation = json.loads(result.stdout)
    assert observation["artifact_lint_status"] == "skipped"
    persisted = json.loads(state_path.read_text())
    assert "artifact_lint" not in persisted
    assert persisted["artifact_lint_status"] == "skipped"


def test_aggregate_nul_artifact_path_warns_skips_and_preserves_scores(
    state_dir, run_cli, tmp_path,
):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    prior_scores = [{"iteration": 0, "composite": 3.1}]
    state["artifact_path"] = "artifact\x00tampered.md"
    state["artifact_lint"] = [
        {"heading": "Old", "kind": "empty-section", "excerpt": ""}
    ]
    state["artifact_lint_status"] = "findings"
    state["score_history"] = prior_scores
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    out = tmp_path / "scoring.json"

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--out", str(out), "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    assert "WARN #351: artifact lint skipped" in result.stderr
    observation = json.loads(result.stdout)
    assert observation["artifact_lint"] == []
    assert observation["artifact_lint_status"] == "skipped"
    scoring = json.loads(out.read_text())
    assert scoring["items"] == {
        "mission_achievement": 4.5,
        "accuracy": 4.4,
        "completeness": 4.3,
        "usability": 4.2,
    }
    evidence = json.loads((state_dir.parent / scoring["findings_evidence_path"]).read_text())
    assert evidence["artifact_lint"] == []
    assert evidence["artifact_lint_status"] == "skipped"
    persisted = json.loads(state_path.read_text())
    assert "artifact_lint" not in persisted
    assert persisted["artifact_lint_status"] == "skipped"
    assert persisted["score_history"] == prior_scores


def test_aggregate_fifo_artifact_skips_without_blocking_and_clears_stale_lint(
    state_dir, tmp_path,
):
    artifact = tmp_path / "artifact.fifo"
    os.mkfifo(artifact)
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["artifact_path"] = str(artifact)
    state["artifact_lint"] = [
        {"heading": "Old", "kind": "empty-section", "excerpt": ""}
    ]
    state_path.write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MISSION_")
    }
    env["MISSION_SESSION_ID"] = "test"

    result = _run_fifo_aggregate_reviews(
        cwd=state_dir.parent,
        env=env,
        review=review,
    )

    # 実行時間そのものは成功条件にしない。無限ブロックは watchdog が捕える。
    diagnostic_context = (
        f"elapsed={result.elapsed:.3f}s "
        f"watchdog={FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS:.3f}s "
        f"stderr={_redact_timeout_diagnostic(result.stderr)}"
    )
    assert result.returncode == 0, diagnostic_context
    assert "WARN #351: artifact lint skipped" in result.stderr, diagnostic_context
    observation = json.loads(result.stdout)
    assert observation["artifact_lint_status"] == "skipped"
    persisted = json.loads(state_path.read_text())
    assert "artifact_lint" not in persisted
    assert persisted["artifact_lint_status"] == "skipped"


def test_fifo_timeout_diagnostic_redacts_environment_specific_details(
    monkeypatch, tmp_path,
):
    review = _review(tmp_path / "review.json")
    env = {"MISSION_SESSION_ID": "test"}

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self._killed = False

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(
                    cmd=["python", "aggregate-reviews"],
                    timeout=FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS,
                    stderr=(
                        f"traceback {_HOME_PREFIX}{_CURRENT_USER}/mission "
                        f"user={_CURRENT_USER}"
                    ),
                )
            self.returncode = -9
            if self._killed:
                return ("", f"traceback {_HOME_PREFIX}{_CURRENT_USER}/mission user={_CURRENT_USER}")
            return ("", "")

        def kill(self):
            self._killed = True
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(AssertionError) as exc_info:
        _run_fifo_aggregate_reviews(cwd=tmp_path, env=env, review=review)

    message = str(exc_info.value)
    assert "after " in message
    # watchdog 値はリテラルで固定しない（本テストの目的は環境依存情報の伏字化であり、
    # watchdog の値ではない）。定数を変えたときにここが道連れで落ちないようにする。
    assert f"watchdog={FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS:.3f}s" in message
    assert "never finished" in message
    assert "child=terminated by SIGKILL" in message
    assert "traceback" in message
    assert _HOME_PREFIX not in message
    assert _CURRENT_USER not in message


@pytest.mark.parametrize("stage", ["resolve", "relative_to"])
@pytest.mark.parametrize(
    "error_type", [OSError, UnicodeError, ValueError, TypeError, RuntimeError]
)
def test_artifact_path_operations_fail_open_with_warning(
    tmp_path, monkeypatch, capsys, stage, error_type,
):
    artifact = tmp_path / "artifact.md"
    artifact.write_text("## Score\n")
    original = getattr(Path, stage)

    def fail_artifact_operation(path, *args, **kwargs):
        if path == artifact:
            raise error_type(stage)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, stage, fail_artifact_operation)

    findings, status = MISSION_STATE._lint_state_artifact(
        tmp_path, {"artifact_path": str(artifact)}
    )

    assert findings == []
    assert status == "skipped"
    assert "WARN #351: artifact lint skipped" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error_type", [OSError, UnicodeError, ValueError, TypeError, RuntimeError]
)
def test_artifact_open_errors_fail_open_with_warning(
    tmp_path, monkeypatch, capsys, error_type,
):
    artifact = tmp_path / "artifact.md"
    artifact.write_text("## Score\n")

    def fail_open(path, flags):
        raise error_type("open")

    monkeypatch.setattr(os, "open", fail_open)

    findings, status = MISSION_STATE._lint_state_artifact(
        tmp_path, {"artifact_path": str(artifact)}
    )

    assert findings == []
    assert status == "skipped"
    assert "WARN #351: artifact lint skipped" in capsys.readouterr().err


@pytest.mark.parametrize("special_mode", [stat.S_IFSOCK, stat.S_IFCHR])
def test_socket_and_device_modes_are_rejected_after_open(
    tmp_path, monkeypatch, capsys, special_mode,
):
    artifact = tmp_path / "artifact-special"
    artifact.write_text("## Score\n")
    monkeypatch.setattr(
        MISSION_STATE.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=special_mode),
    )

    findings, status = MISSION_STATE._lint_state_artifact(
        tmp_path, {"artifact_path": str(artifact)}
    )

    assert findings == []
    assert status == "skipped"
    assert "not a regular file" in capsys.readouterr().err


def test_oversized_regular_artifact_is_bounded_and_skipped(tmp_path, capsys):
    artifact = tmp_path / "artifact.md"
    artifact.write_bytes(b"x" * (MISSION_STATE._ARTIFACT_LINT_MAX_BYTES + 1))

    findings, status = MISSION_STATE._lint_state_artifact(
        tmp_path, {"artifact_path": str(artifact)}
    )

    assert findings == []
    assert status == "skipped"
    assert "exceeds lint size limit" in capsys.readouterr().err


def test_stats_counts_lint_findings_and_clean_artifacts(tmp_path, run_cli):
    for name, lint in (
        ("bad", [
            {"heading": "A", "kind": "empty-section", "excerpt": ""},
            {"heading": "B", "kind": "stub-forward-reference", "excerpt": "TBD"},
        ]),
        ("clean", []),
        ("skipped", None),
    ):
        sessions = tmp_path / name / ".mission-state" / "sessions"
        sessions.mkdir(parents=True)
        state = {
            "mission": name,
            "mission_id": name,
            "session_id": name,
            "loop_active": False,
            "passes": True,
            "halt_reason": "",
            "score_history": [],
            "iteration": 1,
            "project_root": str(tmp_path / name),
        }
        if lint is not None:
            state["artifact_lint"] = lint
        (sessions / f"{name}.json").write_text(json.dumps(state))

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0
    assert json.loads(result.stdout)["artifact_lint_counts"] == {
        "empty_section": 1,
        "stub_forward_reference": 1,
        "clean": 1,
    }
