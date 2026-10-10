"""A compatibility command must retain the lease admitted by its repository."""

import subprocess

import pytest

from . import test_issue550_c2_stage_b_batch2 as batch2
from . import test_issue550_c2_stage_b_batch3 as batch3


_CLOCK_BOOTSTRAP = r'''
import importlib.util
import sys
from datetime import timedelta

path, command_type = sys.argv[1:3]
spec = importlib.util.spec_from_file_location("mission_state_989_clock", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
current = [module._lease_now()]
module._lease_now = lambda: current[0]
begin = module.LocalFencedRepository.begin

def advance_after_admission(repository, request):
    targeted = request.audit.command_type == command_type and (
        command_type != "specialists-invoke-command"
        or request.operation_id.endswith(":reserve")
    )
    if targeted:
        base = repository.read(request.session_id).state.lease
        expiry = module.parse_iso_datetime(base.lease_expires_at)
        # Setup helpers may extend TTL to an hour. Admit just beyond the
        # renewal threshold so the next second changes expiry in every case.
        current[0] = max(current[0], expiry - timedelta(
            seconds=repository.lease_ttl_seconds - 1))
    admitted = begin(repository, request)
    if targeted:
        current[0] += timedelta(seconds=1)
    return admitted

module.LocalFencedRepository.begin = advance_after_admission
sys.argv = [path, *sys.argv[3:]]
module.main()
'''


@pytest.mark.parametrize("command_type, prefix, exercise", [
    ("specialists-invoke-command", ["specialists", "invoke-prepared"],
     batch2.test_specialists_invoke_command_no_double_dispatch_on_replay),
    ("specialists-reconcile-invocation", ["specialists", "reconcile-invocation"],
     batch2.test_specialists_reconcile_invocation_v5_preserves_head_and_replays),
    ("specialists-plan-import", ["specialists", "plan-import"],
     batch2.test_specialists_plan_import_v5_preserves_head_and_replays),
    ("manual-score-capture", ["manual-score-capture"],
     batch3.test_manual_score_capture_v5_preserves_head_and_replays),
], ids=["invoke", "reconcile", "plan-import", "manual-score"])
def test_pending_lease_survives_a_clock_second(
    command_type, prefix, exercise, raw_run_cli, tmp_path,
    isolated_provider_python, monkeypatch,
):
    """Reuse CLI contracts; advance the child clock only after lease admission.

    A repeated legacy renewal used to alter expiry and fail before commit.
    Each case also checks replay; invoke checks the provider is called once.
    No scheduling delay, sleep, shared state, or production clock hook is used.
    """
    launch = subprocess.run

    def run_with_clock(command, **options):
        if command[2:2 + len(prefix)] == prefix:
            command = [command[0], "-c", _CLOCK_BOOTSTRAP,
                       command[1], command_type, *command[2:]]
        return launch(command, **options)

    monkeypatch.setattr(subprocess, "run", run_with_clock)
    options = {"run_cli": raw_run_cli, "tmp_path": tmp_path}
    if command_type == "specialists-invoke-command":
        options["isolated_provider_python"] = isolated_provider_python
    exercise(**options)
