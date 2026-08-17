"""#188/#506: phase authority belongs exclusively to transition commands."""

import pytest


@pytest.mark.parametrize(
    "value",
    ("executing", "bogus", "execution", "review", "plan", "score"),
)
def test_set_phase_rejects_every_value_without_mutating_state(
    state_dir, run_cli, value
):
    state_path = next((state_dir / "sessions").glob("*.json"))
    before = state_path.read_bytes()

    result = run_cli("set", f"phase={value}", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "専用command" in result.stderr
    assert state_path.read_bytes() == before


def test_advance_accepts_a_valid_phase_and_tracks_duration(
    state_dir, run_cli, read_state
):
    run_cli(
        "advance",
        "--phase",
        "executing",
        "--activity",
        "active:implementation",
        cwd=state_dir.parent,
        check=True,
    )
    run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        cwd=state_dir.parent,
        check=True,
    )

    state = read_state(state_dir)
    assert state["phase"] == "reviewing"
    assert "executing" in state.get("phase_durations_sec", {})
