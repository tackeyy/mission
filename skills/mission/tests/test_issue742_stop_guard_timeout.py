"""Issue #742: the Stop guard bounds itself instead of depending on external commands.

D2: `stop-verdict` applies its own limit, so hosts without `timeout` and without
`perl` are still bounded -- on platforms that have `SIGALRM`. Where they do not, the
shell adapter's external limit, or failing that the host's hook timeout, is what
remains; the inner limit is not unconditional.
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

    @pytest.mark.parametrize("raw", ["1", "2", "5", "8"])
    def test_the_accepted_values_are_honoured(self, raw):
        assert resolve_guard_timeout(raw) == int(raw)

    @pytest.mark.parametrize("raw", ["9", "30", "600"])
    def test_a_value_above_the_ceiling_falls_back(self, raw):
        """Honouring it would let the host's 10s cut first, and the block never lands."""
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    @pytest.mark.parametrize("raw", ["08", "01", " 8 ", "8 "])
    def test_forms_the_shell_cannot_accept_fall_back_here_too(self, raw):
        """The two validations must agree on every input, not just the obvious ones."""
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    @pytest.mark.parametrize(
        "raw",
        # `٢` (Arabic-Indic 2), not `٨`: `int("٨")` is 8, which equals the default, so
        # weakening the check to `str.isdigit()` would still pass the assertion.
        [None, "", "   ", "-1", "abc", "8.0", "8s", "1e3", "+8", "٢"],
    )
    def test_everything_else_falls_back_to_the_default(self, raw):
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    def test_zero_does_not_disable_the_limit(self):
        """`perl -e 'alarm 0'` silently disables the alarm; the resolver must not."""
        assert resolve_guard_timeout("0") == DEFAULT_GUARD_TIMEOUT_SECONDS

    def test_a_bad_value_is_not_turned_into_a_block(self):
        """D3 says fall back to the default, not fail closed on the value itself."""
        assert resolve_guard_timeout("nonsense") == DEFAULT_GUARD_TIMEOUT_SECONDS


HAS_SIGALRM = hasattr(signal, "SIGALRM")


@pytest.mark.skipif(not HAS_SIGALRM, reason="the in-process limit needs SIGALRM")
class TestGuardTimeLimit:
    """D2: the limit is applied in-process, where the platform provides SIGALRM."""

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

    def test_an_outer_alarm_keeps_its_remaining_time(self):
        """Restoring only the handler would silently cancel a limit set further out."""
        try:
            signal.signal(signal.SIGALRM, lambda *_: None)
            signal.alarm(30)
            with guard_time_limit(2):
                pass
            assert signal.alarm(0) > 0, "the outer alarm must survive the inner limit"
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def test_the_outer_deadline_is_not_extended_by_the_inner_block(self):
        """Restoring "what is left" as whole seconds grants more time than remained.

        `int()` drops the fraction and a floor of 1 re-arms an expired deadline, so the
        outer alarm fires late. Measured against the deadline, not the remainder.
        """
        fired = []
        try:
            signal.signal(signal.SIGALRM, lambda *_: fired.append(time.monotonic()))
            armed = time.monotonic()
            signal.alarm(2)                  # outer deadline: armed + 2.0
            with guard_time_limit(5):        # inner asks for longer
                time.sleep(0.8)
            while not fired and time.monotonic() - armed < 4:
                time.sleep(0.02)
            assert fired, "the outer alarm must still fire"
            overshoot = fired[0] - (armed + 2.0)
            assert overshoot < 0.3, (
                "the outer deadline moved by {:.3f}s; it must not be extended".format(
                    overshoot
                )
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def test_an_expired_outer_deadline_is_not_re_armed(self):
        """If the block outlives the outer deadline, there is nothing left to restore."""
        try:
            signal.signal(signal.SIGALRM, lambda *_: None)
            signal.alarm(1)
            with pytest.raises(GuardTimeout):
                with guard_time_limit(5):
                    time.sleep(3)
            assert signal.alarm(0) == 0, "an expired deadline must not be re-armed"
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def test_a_shorter_outer_alarm_is_not_overridden(self):
        """The shell's `perl -e 'alarm N'` is inherited across exec.

        Setting the inner limit outright would replace it: a 5s inner limit would let a
        2s body run to completion under a 1s outer deadline, and the outer limit would
        be lost for the whole call. The two limits have to stay independent.
        """
        fired = []
        try:
            signal.signal(signal.SIGALRM, lambda *_: fired.append(time.monotonic()))
            signal.alarm(1)          # the outer limit, as perl would set it
            started = time.monotonic()
            with pytest.raises(GuardTimeout):
                with guard_time_limit(5):   # asking for longer than the outer deadline
                    time.sleep(4)
            elapsed = time.monotonic() - started
            assert elapsed < 3, (
                "the inner limit must not extend the outer deadline "
                "(took {:.1f}s)".format(elapsed)
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def test_clears_the_alarm_even_when_the_body_raises(self):
        with pytest.raises(ValueError):
            with guard_time_limit(30):
                raise ValueError("boom")
        assert signal.alarm(0) == 0


@pytest.mark.skipif(not HAS_SIGALRM, reason="the in-process limit needs SIGALRM")
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


class TestTheLimitIsAppliedTwice:
    """Neither limit alone closes the hole; the PR keeps both."""

    def test_the_outer_limit_survives_because_714_needs_it(self):
        """#714 hangs a substituted `MISSION_STATE_PY`, which has no inner limit.

        Removing the outer limit would satisfy #742 on this repo's own command while
        breaking the contract for any other command the hook is pointed at.
        """
        source = GUARD_SH.read_text(encoding="utf-8")
        assert "command -v timeout" in source
        assert "perl -e" in source

    def test_the_fallback_branch_is_no_longer_unbounded(self):
        """The `else` used to run unbounded; now the command bounds itself (#742 D2)."""
        state_source = STATE_PY.read_text(encoding="utf-8")
        assert "@bounded_by_guard_timeout" in state_source, (
            "the else branch relies on the command applying its own limit"
        )

    def test_the_hook_holds_no_numeric_judgment(self):
        """#615: `analyze_guard_shell` rejects numeric comparison as a policy decision."""
        source = GUARD_SH.read_text(encoding="utf-8")
        assert not re.search(
            r"(?:\[\[?|\btest\b)[^\n]*(?:-lt|-le|-gt|-ge)\b", source
        )

    @pytest.mark.parametrize("raw", ["abc", "0", "-5", "", "8s", "9", "600", "08", "01"])
    def test_a_bad_value_never_reaches_the_external_limit(self, raw):
        """`timeout abc ...` fails outright, so the fallback has to happen first."""
        script = GUARD_SH.read_text(encoding="utf-8")
        block = script.split('MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"', 1)[1]
        block = block.split("export MISSION_STATE_TIMEOUT", 1)[0]
        probe = (
            'MISSION_STATE_TIMEOUT="{}"\n'.format(raw)
            + 'MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"\n'
            + block
            + '\nprintf %s "$MISSION_STATE_TIMEOUT"\n'
        )
        result = subprocess.run(["/bin/bash", "-c", probe], capture_output=True, text=True)
        assert result.stdout == "8"
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    def test_both_sides_bound_a_valid_value(self):
        """A value both sides accept must not be silently replaced by either."""
        script = GUARD_SH.read_text(encoding="utf-8")
        block = script.split('MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"', 1)[1]
        block = block.split("export MISSION_STATE_TIMEOUT", 1)[0]
        for value in ("1", "5", "8"):
            probe = (
                'MISSION_STATE_TIMEOUT="{}"\n'.format(value)
                + 'MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"\n'
                + block
                + '\nprintf %s "$MISSION_STATE_TIMEOUT"\n'
            )
            result = subprocess.run(["/bin/bash", "-c", probe], capture_output=True, text=True)
            assert result.stdout == value
            assert resolve_guard_timeout(value) == int(value)

    def test_bounded_without_timeout_and_without_perl(self, tmp_path):
        """D2: the inner limit covers the environment the outer one cannot."""
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
