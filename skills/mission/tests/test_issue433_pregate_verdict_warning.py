"""Issue #433: pregate verdict が accepted 以外のときの警告と guidance."""

from __future__ import annotations

import json
from pathlib import Path


def _record_payload(*, issue_ref: str, verdict: str) -> str:
    return json.dumps(
        {
            "schema": "mission-pregate-evaluation/1",
            "issue_ref": issue_ref,
            "subject_digest": "sha256:" + "1" * 64,
            "evaluated_at": "2026-08-13T00:00:00Z",
            "ttl_hours": 72,
            "verdict": verdict,
            "gate_id": "planning-check",
            "evidence_refs": [{"kind": "path", "value": ".mission-state/archive/evidence.json"}],
            "producer_session": "session-1",
            "payload": {"detail": "fixture"},
        },
        ensure_ascii=False,
    )


def _prepare_pregate(run_cli, root: Path, verdict: str) -> None:
    input_path = root / f"pregate-{verdict}.json"
    input_path.write_text(_record_payload(issue_ref="433", verdict=verdict), encoding="utf-8")
    run_cli("pregate", "record", "--issue-ref", "433", "--input", str(input_path), cwd=root, check=True)


def _init_state(run_cli, root: Path):
    return run_cli(
        "init",
        "issue 433 mission",
        "--complexity",
        "Standard",
        "--issue-ref",
        "433",
        cwd=root,
        check=True,
    )


def _warning_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("WARNING:")]


def test_init_warns_when_pregate_verdict_is_split_required(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    _prepare_pregate(run_cli, root, "split-required")

    result = _init_state(run_cli, root)

    assert result.returncode == 0
    assert result.stdout.strip()
    assert _warning_lines(result.stderr) == [
        "WARNING: pregate verdict=split-required。planning 前に分割を解決してください"
    ]
    assert "WARNING" not in result.stdout


def test_init_warns_when_pregate_verdict_is_rejected(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    _prepare_pregate(run_cli, root, "rejected")

    result = _init_state(run_cli, root)

    assert result.returncode == 0
    assert _warning_lines(result.stderr) == [
        "WARNING: pregate verdict=rejected。planning 前に分割を解決してください"
    ]
    assert "WARNING" not in result.stdout


def test_init_is_silent_for_accepted_or_missing_pregate(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    _prepare_pregate(run_cli, root, "accepted")

    accepted = _init_state(run_cli, root)
    assert accepted.returncode == 0
    assert _warning_lines(accepted.stderr) == []
    assert "WARNING" not in accepted.stdout

    other_root = tmp_path / "missing"
    other_root.mkdir()
    (other_root / ".mission-state").mkdir()
    missing = _init_state(run_cli, other_root)
    assert missing.returncode == 0
    assert _warning_lines(missing.stderr) == []
    assert "WARNING" not in missing.stdout


def test_next_includes_pregate_warning_in_planning_guidance_for_non_accepted_verdict(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    _prepare_pregate(run_cli, root, "split-required")
    _init_state(run_cli, root)

    result = run_cli("next", cwd=root, check=True)
    out = json.loads(result.stdout)

    assert result.returncode == 0
    assert "pregate verdict=split-required。planning 前に分割を解決してください" in out["summary"]
    assert out["next_action"] == "plan-inline"
    assert result.stderr == ""


def test_next_is_silent_for_accepted_or_missing_pregate(run_cli, tmp_path):
    accepted_root = tmp_path / "accepted"
    accepted_root.mkdir()
    (accepted_root / ".mission-state").mkdir()
    _prepare_pregate(run_cli, accepted_root, "accepted")
    _init_state(run_cli, accepted_root)

    accepted = run_cli("next", cwd=accepted_root, check=True)
    accepted_out = json.loads(accepted.stdout)
    assert accepted.returncode == 0
    assert "pregate verdict" not in accepted_out["summary"]
    assert accepted.stderr == ""

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    (missing_root / ".mission-state").mkdir()
    _init_state(run_cli, missing_root)
    missing = run_cli("next", cwd=missing_root, check=True)
    missing_out = json.loads(missing.stdout)
    assert missing.returncode == 0
    assert "pregate verdict" not in missing_out["summary"]
    assert missing.stderr == ""
