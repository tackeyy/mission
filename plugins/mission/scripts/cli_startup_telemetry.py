#!/usr/bin/env python3
"""CLI 起動コストの分解計測 (#704).

#702 は「full suite の CPU のうち、テスト本体ではなく CLI プロセスの起動と
top-level import に費やされる分をどれだけ削れるか」を判断する。その判断には
次の 3 つが要る。

1. **動的な呼び出し回数** — 静的な callsite 数では実行回数が分からない
2. **CPU の分解** — `--help` の総コストを代表値にすると、prefork でも消えない
   parser 構築と handler 実行まで削減余地に数えてしまい、過大評価になる
3. **同一条件での再測定** — 改善の前後を同じ command mix で比べる

壁時間ではなく CPU 時間を基準にする。壁時間は測定間で 502〜1,136 秒とばらつく
一方、CPU は 2,410〜2,530 秒で安定していた（#702 の実測）。

使い方:

    # 1. 呼び出し回数を集める (pytest plugin として)
    PYTHONPATH=scripts CLI_TELEMETRY_DIR=/tmp/counts \\
      pytest -q -n auto --dist loadfile -p cli_startup_telemetry skills/mission

    # 2. 1 回あたりの CPU を分解して測る
    python3 scripts/cli_startup_telemetry.py microbench --json > /tmp/micro.json

    # 3. 停止ゲートを判定する
    python3 scripts/cli_startup_telemetry.py report \\
      --counts-dir /tmp/counts --microbench /tmp/micro.json --suite-cpu 2410
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
MISSION_STATE_PY = ROOT / "skills" / "mission" / "bin" / "mission-state.py"
MISSION_LIB = ROOT / "skills" / "mission" / "lib"

COUNTER_ENV_VAR = "CLI_TELEMETRY_DIR"
NO_SUBCOMMAND = "<no-subcommand>"
DEFAULT_GATE_THRESHOLD = 0.15


class TelemetryError(RuntimeError):
    """計測が成立しなかったことを示す。値の欠落を 0 と混同しないための型。"""


# --- 呼び出しの分類 -------------------------------------------------------


# ``-c`` / ``-m`` は後続を「実行対象」として奪うため、これらが現れた時点で
# そのプロセスはスクリプトを実行していない。値を取る他のオプションは読み飛ばす。
_INTERPRETER_TERMINATORS = {"-c", "-m"}
_INTERPRETER_VALUE_OPTIONS = {"-X", "-W", "--check-hash-based-pycs"}


def classify_invocation(argv: Any) -> str | None:
    """``mission-state.py`` を**スクリプトとして起動**したときだけ分類する。

    サブコマンドが無い呼び出し (``--help`` 等) は ``<no-subcommand>`` として
    区別する。落とすと「計測されなかった」のか「サブコマンドが無かった」のか
    分からなくなる。

    ファイル名の一致だけで判定してはならない。``git diff <path>`` のように
    別コマンドの**引数**としてパスが現れるだけの呼び出しまで計上してしまい、
    回数が水増しされる。この回数は #702 の停止ゲートの一次入力なので、
    水増しはそのまま誤った意思決定になる。
    """
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Iterable):
        return None
    try:
        parts = [os.fsdecode(item) for item in argv]
    except (TypeError, ValueError):
        return None
    if not parts:
        return None

    # 直接実行 (`./mission-state.py init`) はそれ自体が argv[0] になる。
    if Path(parts[0]).name == MISSION_STATE_PY.name:
        for candidate in parts[1:]:
            if not candidate.startswith("-"):
                return candidate
        return NO_SUBCOMMAND

    # それ以外は Python インタープリタ経由の起動だけを対象にする。`cat <path>` の
    # ように別コマンドの引数としてパスが現れるものを除くための条件。
    if not Path(parts[0]).name.startswith("python"):
        return None

    index = 1
    while index < len(parts):
        part = parts[index]
        if part in _INTERPRETER_TERMINATORS:
            return None
        if part in _INTERPRETER_VALUE_OPTIONS:
            index += 2
            continue
        if part.startswith("-") and part != "-":
            index += 1
            continue
        break
    else:
        return None

    if Path(parts[index]).name != MISSION_STATE_PY.name:
        return None
    for candidate in parts[index + 1:]:
        if not candidate.startswith("-"):
            return candidate
    return NO_SUBCOMMAND


class InvocationCounter:
    """``subprocess.Popen`` を計測点にして CLI 呼び出しを数える。

    計測点を ``subprocess.run`` に置くと ``Popen`` 直叩きを取りこぼす。#702 の
    baseline 4,678 回はその方式で得た値であり、下限でしかなかった。``run`` は
    内部で ``Popen`` を使うため、``Popen`` 側だけを数えれば二重計上も起きない。
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @contextlib.contextmanager
    def instrument(self):
        original = subprocess.Popen
        counts = self.counts

        class CountingPopen(original):  # type: ignore[misc, valid-type]
            def __init__(self, args, *rest, **kwargs):
                command = classify_invocation(args)
                if command is not None:
                    counts[command] = counts.get(command, 0) + 1
                super().__init__(args, *rest, **kwargs)

        subprocess.Popen = CountingPopen  # type: ignore[misc]
        try:
            yield self
        finally:
            subprocess.Popen = original  # type: ignore[misc]

    def dump(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{os.getpid()}.json"
        path.write_text(json.dumps(self.counts, sort_keys=True))
        return path


def aggregate_counts(directory: Path) -> dict[str, int]:
    """worker 別の集計ファイルを合算する。

    ファイルが 1 つも無い場合は例外にする。空の集計を「呼び出し 0 回」と読むと、
    計測に失敗しただけの run が「削減余地なし」と判定されてしまう。
    """
    files = sorted(Path(directory).glob("*.json"))
    if not files:
        raise TelemetryError(f"no counter files under {directory}")
    totals: dict[str, int] = {}
    for path in files:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise TelemetryError(f"unreadable counter file: {path}") from exc
        if not isinstance(payload, dict):
            raise TelemetryError(f"unreadable counter file: {path}")
        for command, count in payload.items():
            # 形式が正しくても値が壊れていれば止める。素通しすると int() の
            # ValueError がそのまま traceback になり fail-closed が成立しない。
            if isinstance(count, bool) or not isinstance(count, int):
                raise TelemetryError(
                    f"non-integer count in {path}: {command}={count!r}"
                )
            totals[command] = totals.get(command, 0) + count
    return totals


# --- pytest plugin --------------------------------------------------------
# `-p cli_startup_telemetry` で読み込む。xdist の worker ごとに独立したプロセスで
# 動くため、worker ごとに 1 ファイルを書き出して後段で合算する。

_PLUGIN_COUNTER = InvocationCounter()
_PLUGIN_INSTRUMENT = None


def pytest_configure(config):  # pragma: no cover - pytest hook
    global _PLUGIN_INSTRUMENT
    if os.environ.get(COUNTER_ENV_VAR):
        _PLUGIN_INSTRUMENT = _PLUGIN_COUNTER.instrument()
        _PLUGIN_INSTRUMENT.__enter__()


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - pytest hook
    directory = os.environ.get(COUNTER_ENV_VAR)
    if not directory or _PLUGIN_INSTRUMENT is None:
        return
    _PLUGIN_INSTRUMENT.__exit__(None, None, None)
    _PLUGIN_COUNTER.dump(Path(directory))


# --- CPU 分解 -------------------------------------------------------------


def _child_cpu_seconds(code: str, *, repeat: int) -> float:
    """``code`` を別プロセスで ``repeat`` 回動かし、1 回あたりの CPU を返す。

    子プロセスの CPU は ``resource.getrusage(RUSAGE_CHILDREN)`` で拾う。壁時間を
    使わないのは、他プロセスに CPU を奪われて待たされた時間を計上しないため。
    ``os.times()`` は分解能が 10ms しかなく、fork のような短い区間が 0 に潰れる。
    """
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    for _ in range(repeat):
        subprocess.run([sys.executable, "-c", code], capture_output=True, check=False)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    used = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return used / repeat


_IMPORT_CODE = (
    "import sys, importlib.util;"
    f"sys.path.insert(0, {str(MISSION_LIB)!r});"
    f"spec = importlib.util.spec_from_file_location('ms', {str(MISSION_STATE_PY)!r});"
    "m = importlib.util.module_from_spec(spec);"
    "sys.modules['ms'] = m;"
    "spec.loader.exec_module(m)"
)


def microbench(*, repeat: int = 5) -> dict[str, Any]:
    """1 回あたりの CPU を 4 段階＋prefork 参照値に分解する。"""
    interpreter = _child_cpu_seconds("pass", repeat=repeat)
    imported = _child_cpu_seconds(_IMPORT_CODE, repeat=repeat)
    parser = _child_cpu_seconds(_IMPORT_CODE + ";m._build_parser()", repeat=repeat)
    help_run = _child_cpu_seconds(
        _IMPORT_CODE
        + ";sys.argv=['mission-state.py','--help'];"
        "import io, contextlib;"
        "buf=io.StringIO();"
        "\nexec('try:\\n"
        "    import contextlib\\n"
        "    with contextlib.redirect_stdout(buf):\\n"
        "        m.main()\\n"
        "except SystemExit:\\n"
        "    pass')",
        repeat=repeat,
    )
    prefork = _prefork_child_cpu(repeat=repeat)
    return {
        "schema": "mission-cli-startup-microbench/1",
        "repeat": repeat,
        "cpu_seconds": {
            "interpreter_start": interpreter,
            "top_level_import": max(imported - interpreter, 0.0),
            "parser_build": max(parser - imported, 0.0),
            "handler_help": max(help_run - parser, 0.0),
            "prefork_child": prefork,
        },
    }


def _prefork_child_cpu(*, repeat: int) -> float:
    """親で一度だけ import し、呼び出しごとに fork したときの子の CPU。

    prefork へ移しても消えない下限コスト。削減見積りからこれを引く。
    """
    code = (
        _IMPORT_CODE + ";"
        "import os, resource;"
        "R = resource.RUSAGE_CHILDREN\n"
        "before = resource.getrusage(R)\n"
        f"for _ in range({repeat}):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        os._exit(0)\n"
        "    os.waitpid(pid, 0)\n"
        "after = resource.getrusage(R)\n"
        f"print(((after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)) / {repeat})"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    try:
        return max(float(result.stdout.strip().splitlines()[-1]), 0.0)
    except (IndexError, ValueError):
        raise TelemetryError("prefork microbench produced no measurement")


def removable_cpu_per_invocation(cpu_seconds: Mapping[str, float]) -> float:
    """prefork で実際に除去できる 1 回あたりの CPU。

    除去できるのは interpreter 起動と top-level import。parser 構築と handler
    実行は prefork でも残るため含めない。fork 自体のコストは差し引く。
    """
    removable = (
        float(cpu_seconds.get("interpreter_start", 0.0))
        + float(cpu_seconds.get("top_level_import", 0.0))
        - float(cpu_seconds.get("prefork_child", 0.0))
    )
    return max(removable, 0.0)


def build_report(
    *,
    counts: Mapping[str, int],
    removable_cpu_per_invocation: float,
    suite_cpu_seconds: float,
    gate_threshold: float = DEFAULT_GATE_THRESHOLD,
) -> dict[str, Any]:
    if suite_cpu_seconds <= 0:
        raise TelemetryError("suite cpu must be positive")
    invocations = sum(counts.values())
    if invocations <= 0:
        raise TelemetryError("no invocations were counted")

    removable_cpu = invocations * removable_cpu_per_invocation
    share = removable_cpu / suite_cpu_seconds
    return {
        "schema": "mission-cli-startup-report/1",
        "invocations": invocations,
        "command_mix": dict(counts),
        "removable_cpu_per_invocation": removable_cpu_per_invocation,
        "removable_cpu_seconds": removable_cpu,
        "suite_cpu_seconds": suite_cpu_seconds,
        "removable_share": share,
        "gate": {
            "threshold": gate_threshold,
            "verdict": "above-threshold" if share >= gate_threshold else "below-threshold",
        },
    }


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    bench = sub.add_parser("microbench", help="1 回あたりの CPU を分解して測る")
    bench.add_argument("--repeat", type=int, default=5)
    bench.add_argument("--json", action="store_true")

    agg = sub.add_parser("aggregate", help="worker 別の集計ファイルを合算する")
    agg.add_argument("--counts-dir", required=True)

    rep = sub.add_parser("report", help="停止ゲートを判定する")
    rep.add_argument("--counts-dir", required=True)
    rep.add_argument("--microbench", required=True)
    rep.add_argument("--suite-cpu", type=float, required=True)
    rep.add_argument("--gate-threshold", type=float, default=DEFAULT_GATE_THRESHOLD)

    args = parser.parse_args(argv)
    try:
        if args.command == "microbench":
            print(json.dumps(microbench(repeat=args.repeat), ensure_ascii=False, indent=2))
        elif args.command == "aggregate":
            print(json.dumps(aggregate_counts(Path(args.counts_dir)), ensure_ascii=False, indent=2))
        else:
            micro = json.loads(Path(args.microbench).read_text())
            report = build_report(
                counts=aggregate_counts(Path(args.counts_dir)),
                removable_cpu_per_invocation=removable_cpu_per_invocation(micro["cpu_seconds"]),
                suite_cpu_seconds=args.suite_cpu,
                gate_threshold=args.gate_threshold,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
    except (TelemetryError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"cli_startup_telemetry: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
