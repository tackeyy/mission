"""Issue #742: the Stop guard bounds itself instead of depending on external commands.

D2: `stop-verdict` applies its own limit, so hosts without `timeout` and without
`perl` are still bounded.
D3: `MISSION_STATE_TIMEOUT` accepts positive integers only and falls back to the
default (8 seconds) for anything else, without turning a bad value into a block.
"""

from __future__ import annotations

import importlib.util
import json
import re
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_ROOT = REPO_ROOT / "skills" / "mission" / "lib"
GUARD_SH = REPO_ROOT / "scripts" / "mission-stop-guard.sh"
STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"

sys.path.insert(0, str(LIB_ROOT))

from mission_application.guard_timeout import (  # noqa: E402
    DEFAULT_GUARD_TIMEOUT_SECONDS,
    GuardTimeout,
    guard_time_limit,
    resolve_guard_timeout,
)


def _load_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_cli", STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestResolveGuardTimeout:
    """D3: only positive integers are honoured; everything else falls back."""

    def test_default_is_eight_seconds(self):
        assert DEFAULT_GUARD_TIMEOUT_SECONDS == 8

    @pytest.mark.parametrize("raw", ["1", "8", "30", " 12 ", "08"])
    def test_positive_integers_are_honoured(self, raw):
        assert resolve_guard_timeout(raw) == int(raw.strip())

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "-1", "abc", "8.0", "8s", "1e3", "+8", "٨"],
    )
    def test_everything_else_falls_back_to_the_default(self, raw):
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    def test_zero_does_not_disable_the_limit(self):
        """`perl -e 'alarm 0'` silently disables the alarm; the resolver must not."""
        assert resolve_guard_timeout("0") == DEFAULT_GUARD_TIMEOUT_SECONDS

    def test_a_bad_value_is_not_turned_into_a_block(self):
        """D3 says fall back to the default, not fail closed on the value itself."""
        assert resolve_guard_timeout("nonsense") == DEFAULT_GUARD_TIMEOUT_SECONDS


class TestGuardTimeLimit:
    """D2: the limit is applied in-process."""

    def test_raises_when_the_body_exceeds_the_limit(self):
        started = time.monotonic()
        with pytest.raises(GuardTimeout):
            with guard_time_limit(1):
                time.sleep(10)
        assert time.monotonic() - started < 5, "the limit must actually interrupt the body"

    def test_does_not_raise_when_the_body_finishes_in_time(self):
        with guard_time_limit(5):
            pass

    def test_restores_the_previous_handler_and_clears_the_alarm(self):
        sentinel = signal.getsignal(signal.SIGALRM)
        with guard_time_limit(5):
            pass
        assert signal.getsignal(signal.SIGALRM) is sentinel
        assert signal.alarm(0) == 0

    def test_clears_the_alarm_even_when_the_body_raises(self):
        with pytest.raises(ValueError):
            with guard_time_limit(30):
                raise ValueError("boom")
        assert signal.alarm(0) == 0


class TestStopVerdictAppliesTheLimit:
    """The limit reaches the CLI command, not only the helper."""

    def test_a_slow_decision_blocks_instead_of_hanging(self, monkeypatch, tmp_path, capsys):
        """Fail closed when the verdict exceeds the limit."""
        module = _load_state_module()
        monkeypatch.setenv("MISSION_STATE_TIMEOUT", "1")

        def _slow(_request):
            time.sleep(10)

        monkeypatch.setattr(module, "decide_stop_guard", _slow)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))

        args = type(
            "Args",
            (),
            {"hook_input": "-", "prior_decision_fd": None, "state_file": str(tmp_path / "s.json")},
        )()

        started = time.monotonic()
        with pytest.raises(SystemExit) as excinfo:
            module.cmd_stop_verdict(args)
        elapsed = time.monotonic() - started

        assert elapsed < 5, "the command must be bounded by the limit, not by the slow body"
        assert excinfo.value.code == 2
        payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert payload["decision"] == "block"
        assert payload["schema"] == "mission-stop-verdict/1"


class TestShellAdapterHoldsNoPolicy:
    """D2 + #615: the hook dispatches; it does not interpret the value or bound it."""

    def test_the_hook_no_longer_depends_on_timeout_or_perl(self):
        """Their absence used to be a hole; the command now bounds itself."""
        source = GUARD_SH.read_text(encoding="utf-8")
        assert "command -v timeout" not in source
        assert "perl -e" not in source

    def test_the_hook_does_not_interpret_the_limit(self):
        """#615 keeps the hook judgment-free: validating the value is a judgment.

        Two independent validations would also drift apart, and then the two
        limits would disagree on the same input.
        """
        source = GUARD_SH.read_text(encoding="utf-8")
        assert "MISSION_STATE_TIMEOUT:-" not in source, "the default belongs to the resolver"
        assert not re.search(
            r"(?:\[\[?|\btest\b)[^\n]*(?:-lt|-le|-gt|-ge)\b", source
        ), "numeric comparison is a policy decision (#615)"

    def test_bounded_without_timeout_and_without_perl(self, tmp_path):
        """Give the script a PATH that has neither command and confirm it returns."""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in ("python3", "jq", "cat", "sed", "printf", "env", "uname", "date"):
            found = subprocess.run(
                ["/usr/bin/env", "which", name], capture_output=True, text=True
            ).stdout.strip()
            if found:
                (fake_bin / name).symlink_to(found)
        assert (fake_bin / "python3").exists(), "python3 is required for this test"
        assert not (fake_bin / "timeout").exists()
        assert not (fake_bin / "perl").exists()

        env = dict(os.environ)
        env["PATH"] = str(fake_bin)
        env["MISSION_STATE_TIMEOUT"] = "1"

        started = time.monotonic()
        proc = subprocess.run(
            ["/bin/bash", str(GUARD_SH)],
            input="{}",
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 60, "the guard must terminate even without timeout/perl"
        assert proc.stdout.strip(), "the guard must still emit a decision"
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["decision"] in {"block", "approve"}
