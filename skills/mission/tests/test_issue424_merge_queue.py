"""Issue #424: merge queue sidecar と CLI の検査."""

from __future__ import annotations

import json
from pathlib import Path


def _json(result):
    return json.loads(result.stdout)


def _queue_path(root: Path) -> Path:
    return root / ".mission-state" / "merge-queue.json"


def _queue_entries(run_cli, root: Path):
    return _json(run_cli("queue", "status", "--json", cwd=root, check=True))["entries"]


def test_queue_enqueue_next_verify_and_mark_merged_happy_path(state_dir, run_cli):
    root = state_dir.parent

    enqueued = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/99",
            "--head-sha",
            "1" * 40,
            "--base-sha",
            "2" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:00:00Z"},
        )
    )
    assert enqueued["status"] == "ok"
    assert enqueued["queue_id"]
    assert enqueued["entry"]["issue_ref_key"] == "424"
    assert enqueued["entry"]["status"] == "queued"
    assert _queue_path(root).exists()

    next_out = _json(run_cli("queue", "next", "--json", cwd=root, check=True))
    assert next_out["status"] == "ok"
    assert next_out["entry"]["queue_id"] == enqueued["queue_id"]
    assert next_out["entry"]["head_sha"] == "1" * 40

    verify = run_cli(
        "queue",
        "verify",
        "--queue-id",
        enqueued["queue_id"],
        "--current-base-sha",
        "2" * 40,
        cwd=root,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr

    marked = _json(
        run_cli(
            "queue",
            "mark",
            "--queue-id",
            enqueued["queue_id"],
            "--status",
            "merged",
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:00:01Z"},
        )
    )
    assert marked["status"] == "ok"
    assert marked["entry"]["status"] == "merged"


def test_queue_depends_on_blocks_next_until_dependency_is_merged(state_dir, run_cli):
    root = state_dir.parent
    first = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/1",
            "--head-sha",
            "3" * 40,
            "--base-sha",
            "4" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:10:00Z"},
        )
    )
    second = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "425",
            "--pr-ref",
            "https://example.invalid/pr/2",
            "--head-sha",
            "5" * 40,
            "--base-sha",
            "4" * 40,
            "--depends-on",
            "424",
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:10:01Z"},
        )
    )

    next_out = _json(run_cli("queue", "next", "--json", cwd=root, check=True))
    assert next_out["entry"]["queue_id"] == first["queue_id"]
    assert next_out["entry"]["issue_ref_key"] == "424"

    run_cli("queue", "mark", "--queue-id", first["queue_id"], "--status", "merged", cwd=root, check=True)

    next_after = _json(run_cli("queue", "next", "--json", cwd=root, check=True))
    assert next_after["entry"]["queue_id"] == second["queue_id"]
    assert next_after["entry"]["depends_on"] == ["424"]


def test_queue_verify_mismatch_invalidates_entry_and_prints_refreeze_hint(state_dir, run_cli):
    root = state_dir.parent
    enqueued = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/99",
            "--head-sha",
            "6" * 40,
            "--base-sha",
            "7" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:20:00Z"},
        )
    )

    result = run_cli(
        "queue",
        "verify",
        "--queue-id",
        enqueued["queue_id"],
        "--current-base-sha",
        "8" * 40,
        cwd=root,
        check=False,
    )
    assert result.returncode == 2
    assert "refreeze" in result.stderr
    assert "fresh review" in result.stderr

    entries = _queue_entries(run_cli, root)
    assert entries[0]["status"] == "invalidated"
    assert entries[0]["reason"] == "base changed; refreeze required"


def test_queue_reenqueue_supersedes_prior_queued_entry_for_same_issue(state_dir, run_cli):
    root = state_dir.parent
    first = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/1",
            "--head-sha",
            "9" * 40,
            "--base-sha",
            "a" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:30:00Z"},
        )
    )
    second = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/2",
            "--head-sha",
            "b" * 40,
            "--base-sha",
            "a" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:30:01Z"},
        )
    )

    assert first["queue_id"] != second["queue_id"]
    entries = _queue_entries(run_cli, root)
    assert entries[0]["status"] == "superseded"
    assert entries[0]["reason"] == "replaced by newer enqueue"
    assert entries[1]["status"] == "queued"
    assert entries[1]["queue_id"] == second["queue_id"]


def test_queue_mark_rejects_merged_to_merged_transition(state_dir, run_cli):
    root = state_dir.parent
    enqueued = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "424",
            "--pr-ref",
            "https://example.invalid/pr/99",
            "--head-sha",
            "c" * 40,
            "--base-sha",
            "d" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:40:00Z"},
        )
    )
    run_cli("queue", "mark", "--queue-id", enqueued["queue_id"], "--status", "merged", cwd=root, check=True)

    result = run_cli(
        "queue",
        "mark",
        "--queue-id",
        enqueued["queue_id"],
        "--status",
        "merged",
        cwd=root,
        check=False,
    )
    assert result.returncode == 2
    assert "terminal" in result.stderr


def test_queue_status_fails_closed_on_corrupt_file(state_dir, run_cli):
    root = state_dir.parent
    _queue_path(root).write_text("{broken", encoding="utf-8")

    result = run_cli("queue", "status", "--json", cwd=root, check=False)
    assert result.returncode == 2
    assert "valid JSON" in result.stderr or "corrupt" in result.stderr or "unsafe" in result.stderr


def test_queue_rejects_non_hex_sha_and_sanitizes_issue_ref(state_dir, run_cli):
    root = state_dir.parent
    bad = run_cli(
        "queue",
        "enqueue",
        "--issue-ref",
        "../issue/424",
        "--pr-ref",
        "https://example.invalid/pr/99",
        "--head-sha",
        "not-a-sha",
        "--base-sha",
        "e" * 40,
        cwd=root,
        check=False,
    )
    assert bad.returncode == 2

    enqueued = _json(
        run_cli(
            "queue",
            "enqueue",
            "--issue-ref",
            "../issue/424",
            "--pr-ref",
            "https://example.invalid/pr/99",
            "--head-sha",
            "f" * 40,
            "--base-sha",
            "e" * 40,
            cwd=root,
            check=True,
            env_extra={"MISSION_STATE_NOW": "2026-08-13T00:50:00Z"},
        )
    )
    assert enqueued["entry"]["issue_ref_key"] == "___issue_424"
