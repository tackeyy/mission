"""#386: command outcome telemetry is bounded, safe, and observable."""

from __future__ import annotations

import json
import hashlib


def test_stats_and_audit_count_state_and_sidecar_command_outcomes(state_dir, run_cli, tmp_path):
    review = tmp_path / "bad.json"
    review.write_text('{"schema":"wrong"}', encoding="utf-8")

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(review),
        "--event-id", "attempt-2", "--root-event-id", "root-1", "--attempt", "2",
        "--retry-of", "attempt-1", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome"] == {
        "event_id": "attempt-2", "root_event_id": "root-1", "attempt": 2,
        "retry_of": "attempt-1", "command": "review-import", "outcome_kind": "invalid-input",
    }
    stats = json.loads(run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent).stdout)
    assert stats["command_outcome_counts"] == {
        "ok": 0, "expected-gate": 0, "invalid-input": 1, "external": 0,
        "internal-error": 0, "unique_root_events": 1, "retry_count": 1,
        "invalid_records": 0, "corrupt_sidecars": 0,
    }


def test_corrupt_sidecar_is_never_silently_accepted_and_is_visible_in_stats(state_dir, run_cli):
    telemetry = state_dir / "telemetry" / "command-outcomes"
    telemetry.mkdir(parents=True)
    token = hashlib.sha256(b"test").hexdigest()[:16]
    (telemetry / f"{token}.json").write_text("not-json", encoding="utf-8")

    result = run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent)

    assert result.returncode == 0
    assert json.loads(result.stdout)["command_outcome_counts"]["corrupt_sidecars"] == 1
