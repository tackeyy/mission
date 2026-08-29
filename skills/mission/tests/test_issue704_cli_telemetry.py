"""Issue #704: CLI 起動コストの分解計測 (local telemetry).

計測器そのものを先に検証する。誤った計測器は、改善の効果を過大にも過小にも見せる。
とくに #702 の baseline は `subprocess.run` だけを数えており、`Popen` 直叩きを
取りこぼしていた。その欠陥をここで回帰として固定する。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


TELEMETRY_PY = Path(__file__).resolve().parents[3] / "scripts" / "cli_startup_telemetry.py"


def _load():
    spec = importlib.util.spec_from_file_location("cli_startup_telemetry", TELEMETRY_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TELEMETRY = _load()


# --- 呼び出しの分類 -------------------------------------------------------


def test_mission_state_command_name_is_extracted():
    argv = [sys.executable, "/x/skills/mission/bin/mission-state.py", "aggregate-reviews",
            "--iteration", "1"]

    assert TELEMETRY.classify_invocation(argv) == "aggregate-reviews"


def test_leading_options_do_not_hide_the_command_name():
    argv = [sys.executable, "-X", "importtime", "/x/mission-state.py", "--json", "init", "goal"]

    assert TELEMETRY.classify_invocation(argv) == "init"


def test_invocation_without_a_subcommand_is_classified_as_help():
    argv = [sys.executable, "/x/mission-state.py", "--help"]

    assert TELEMETRY.classify_invocation(argv) == "<no-subcommand>"


def test_non_mission_state_subprocess_is_not_counted():
    assert TELEMETRY.classify_invocation(["git", "status"]) is None
    assert TELEMETRY.classify_invocation([sys.executable, "-c", "pass"]) is None


def test_non_sequence_argv_is_ignored_without_raising():
    assert TELEMETRY.classify_invocation("mission-state.py init") is None
    assert TELEMETRY.classify_invocation(None) is None


# --- Popen 直叩きの取りこぼし（#702 baseline の既知の欠陥） ----------------


def test_counter_observes_popen_called_directly(tmp_path):
    """`subprocess.run` だけを計測点にすると Popen 直叩きを取りこぼす。

    これは #702 の baseline (4,678 回) が下限でしかない理由そのもの。
    Popen を計測点にすることで run 経由と直叩きの両方を 1 度ずつ数える。
    """
    counter = TELEMETRY.InvocationCounter()
    script = tmp_path / "mission-state.py"
    script.write_text("import sys; sys.exit(0)\n")

    with counter.instrument():
        subprocess.run([sys.executable, str(script), "init"], capture_output=True)
        process = subprocess.Popen(
            [sys.executable, str(script), "get"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.communicate()

    assert counter.counts == {"init": 1, "get": 1}
    assert counter.total == 2


def test_run_is_counted_once_not_twice(tmp_path):
    """`subprocess.run` は内部で Popen を使う。二重計上してはならない。"""
    counter = TELEMETRY.InvocationCounter()
    script = tmp_path / "mission-state.py"
    script.write_text("import sys; sys.exit(0)\n")

    with counter.instrument():
        subprocess.run([sys.executable, str(script), "next"], capture_output=True)

    assert counter.counts == {"next": 1}


def test_instrumentation_is_restored_even_after_an_error(tmp_path):
    counter = TELEMETRY.InvocationCounter()
    original = subprocess.Popen

    with pytest.raises(RuntimeError):
        with counter.instrument():
            raise RuntimeError("boom")

    assert subprocess.Popen is original


# --- worker 別ファイルの集計 ---------------------------------------------


def test_aggregate_sums_counts_across_workers(tmp_path):
    (tmp_path / "w1.json").write_text(json.dumps({"init": 3, "get": 1}))
    (tmp_path / "w2.json").write_text(json.dumps({"init": 2, "next": 4}))

    aggregated = TELEMETRY.aggregate_counts(tmp_path)

    assert aggregated == {"init": 5, "get": 1, "next": 4}


def test_aggregate_rejects_a_directory_with_no_worker_files(tmp_path):
    """空の集計を「呼び出しゼロ」と読み違えないよう fail-closed にする。"""
    with pytest.raises(TELEMETRY.TelemetryError, match="no counter files"):
        TELEMETRY.aggregate_counts(tmp_path)


def test_aggregate_rejects_a_malformed_worker_file(tmp_path):
    (tmp_path / "w1.json").write_text("{not json")

    with pytest.raises(TELEMETRY.TelemetryError, match="unreadable counter file"):
        TELEMETRY.aggregate_counts(tmp_path)


# --- CPU 分解 -------------------------------------------------------------


def test_microbench_reports_every_stage(tmp_path):
    result = TELEMETRY.microbench(repeat=2)

    assert result["schema"] == "mission-cli-startup-microbench/1"
    for stage in ("interpreter_start", "top_level_import", "parser_build", "handler_help"):
        assert stage in result["cpu_seconds"], stage
        assert result["cpu_seconds"][stage] >= 0
    assert result["repeat"] == 2


def test_microbench_removable_is_import_minus_fork_not_the_help_proxy():
    """除去可能分を `--help` の全コストで代表させない。

    parser 構築と handler 実行は prefork でも消えないため、これを含めると
    削減幅を過大に見積もる（#702 の初回見積り 70% がこの誤り）。
    """
    cpu = {
        "interpreter_start": 0.03,
        "top_level_import": 0.26,
        "parser_build": 0.05,
        "handler_help": 0.06,
        "prefork_child": 0.018,
    }

    removable = TELEMETRY.removable_cpu_per_invocation(cpu)

    # interpreter 起動 + top-level import − fork。parser 構築と handler 実行は含めない。
    assert removable == pytest.approx(0.03 + 0.26 - 0.018)
    # `--help` の全コストを代表値にした場合との差が、過大評価の幅そのもの。
    help_proxy = sum(cpu[stage] for stage in
                     ("interpreter_start", "top_level_import", "parser_build", "handler_help"))
    assert removable < help_proxy - cpu["parser_build"]


def test_removable_cpu_is_never_negative():
    cpu = {"interpreter_start": 0.03, "top_level_import": 0.01,
           "parser_build": 0.05, "handler_help": 0.06, "prefork_child": 0.5}

    assert TELEMETRY.removable_cpu_per_invocation(cpu) == 0.0


# --- 停止ゲートの判定 -----------------------------------------------------


def test_report_computes_share_and_passes_the_gate():
    report = TELEMETRY.build_report(
        counts={"init": 3_000, "get": 1_678},
        removable_cpu_per_invocation=0.242,
        suite_cpu_seconds=2_410.0,
        gate_threshold=0.15,
    )

    assert report["invocations"] == 4_678
    assert report["removable_cpu_seconds"] == pytest.approx(4_678 * 0.242)
    assert report["removable_share"] == pytest.approx(4_678 * 0.242 / 2_410.0)
    assert report["gate"]["threshold"] == 0.15
    assert report["gate"]["verdict"] == "above-threshold"


def test_report_below_threshold_stops_the_prefork_track():
    report = TELEMETRY.build_report(
        counts={"init": 100},
        removable_cpu_per_invocation=0.242,
        suite_cpu_seconds=2_410.0,
        gate_threshold=0.15,
    )

    assert report["gate"]["verdict"] == "below-threshold"


def test_report_rejects_a_non_positive_suite_cpu():
    with pytest.raises(TELEMETRY.TelemetryError, match="suite cpu"):
        TELEMETRY.build_report(
            counts={"init": 1},
            removable_cpu_per_invocation=0.2,
            suite_cpu_seconds=0.0,
            gate_threshold=0.15,
        )


def test_report_rejects_empty_counts():
    """0 件の集計を「削減余地なし」と判定させない（計測失敗と区別する）。"""
    with pytest.raises(TELEMETRY.TelemetryError, match="no invocations"):
        TELEMETRY.build_report(
            counts={},
            removable_cpu_per_invocation=0.2,
            suite_cpu_seconds=2_410.0,
            gate_threshold=0.15,
        )


def test_report_records_the_command_mix_for_reproducible_comparison():
    """改善後の再測定を同一条件で比較するには command mix の一致確認が要る。"""
    report = TELEMETRY.build_report(
        counts={"init": 3, "get": 1},
        removable_cpu_per_invocation=0.2,
        suite_cpu_seconds=100.0,
        gate_threshold=0.15,
    )

    assert report["command_mix"] == {"init": 3, "get": 1}
    assert report["schema"] == "mission-cli-startup-report/1"


# --- CLI 表面 -------------------------------------------------------------


def test_cli_exits_non_zero_when_inputs_are_missing(tmp_path):
    result = subprocess.run(
        [sys.executable, str(TELEMETRY_PY), "aggregate", "--counts-dir", str(tmp_path)],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "no counter files" in result.stderr
