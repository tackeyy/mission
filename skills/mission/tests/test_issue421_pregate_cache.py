"""Issue #421: pregate cache の CLI と init 連携の検査."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skills.mission.lib.pregate_cache import subject_digest as compute_subject_digest

# Shared test clock: capture UTC once per process so payloads and injected CLI
# state use the same reference time across a test run.
_TEST_NOW = datetime.now(timezone.utc)


def _cache_dir(root: Path) -> Path:
    return root / ".mission-state" / "pregate"


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_env() -> dict[str, str]:
    return {"MISSION_STATE_NOW": _iso_utc(_TEST_NOW)}


def _record_payload(
    *,
    issue_ref: str,
    subject_digest: str,
    evaluated_at: str | None = None,
    age_hours: int = 0,
    ttl_hours: int = 72,
    verdict: str = "accepted",
    gate_id: str = "planning-check",
    evidence_refs: list[dict] | None = None,
    producer_session: str = "session-1",
    payload: dict | None = None,
) -> str:
    if evaluated_at is None:
        evaluated_at = _iso_utc(_TEST_NOW - timedelta(hours=age_hours))
    return json.dumps(
        {
            "schema": "mission-pregate-evaluation/1",
            "issue_ref": issue_ref,
            "subject_digest": subject_digest,
            "evaluated_at": evaluated_at,
            "ttl_hours": ttl_hours,
            "verdict": verdict,
            "gate_id": gate_id,
            "evidence_refs": evidence_refs if evidence_refs is not None else [{"kind": "path", "value": ".mission-state/archive/evidence.json"}],
            "producer_session": producer_session,
            "payload": payload if payload is not None else {"detail": "fixture"},
        },
        ensure_ascii=False,
    )


def _json(result):
    return json.loads(result.stdout)


def test_pregate_record_and_check_hit(state_dir, run_cli):
    root = state_dir.parent
    input_path = root / "pregate.json"
    input_path.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "1" * 64, age_hours=1),
        encoding="utf-8",
    )

    recorded = _json(run_cli("pregate", "record", "--issue-ref", "421", "--input", str(input_path), cwd=root, check=True))
    assert recorded["subject_digest"] == "sha256:" + "1" * 64
    assert recorded["path"].startswith(str(_cache_dir(root)))

    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "421",
            "--subject-digest",
            "sha256:" + "1" * 64,
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked["status"] == "hit"
    assert checked["record"]["issue_ref"] == "421"
    assert checked["record"]["subject_digest"] == "sha256:" + "1" * 64


def test_pregate_digest_returns_canonical_subject_digest_for_file_input(state_dir, run_cli):
    root = state_dir.parent
    input_path = root / "pregate-digest.json"
    payload = {"z": 1, "a": ["x", {"b": False}]}
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    digested = _json(run_cli("pregate", "digest", "--input", str(input_path), cwd=root, check=True))
    assert digested == {"subject_digest": compute_subject_digest(payload)}


def test_pregate_digest_supports_stdin_and_seeds_record_check(state_dir, run_cli):
    root = state_dir.parent
    snapshot = {"body": "hello", "title": "issue 432"}
    digest = _json(
        run_cli(
            "pregate",
            "digest",
            "--input",
            "-",
            cwd=root,
            check=True,
            input_text=json.dumps(snapshot, ensure_ascii=False),
        )
    )
    assert digest == {"subject_digest": compute_subject_digest(snapshot)}

    evaluation = root / "pregate-evaluation.json"
    evaluation.write_text(
        _record_payload(issue_ref="432", subject_digest=digest["subject_digest"], payload=snapshot, age_hours=2),
        encoding="utf-8",
    )
    run_cli("pregate", "record", "--issue-ref", "432", "--input", str(evaluation), cwd=root, check=True)

    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "432",
            "--subject-digest",
            digest["subject_digest"],
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked["status"] == "hit"
    assert checked["record"]["payload"] == snapshot


def test_pregate_digest_rejects_invalid_json_input(state_dir, run_cli):
    root = state_dir.parent
    result = run_cli("pregate", "digest", "--input", "-", cwd=root, input_text="not-json")

    assert result.returncode == 2
    assert "ERROR:" in result.stderr


def test_pregate_digest_succeeds_without_mission_state_dir(run_cli, tmp_path):
    root = tmp_path
    payload = {"summary": "digest without state"}
    input_path = root / "pregate-digest.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert not (root / ".mission-state").exists()
    # Issue #444: .mission-state 不在でも digest 計算のみは許可される設計要件 (#432) を固定
    result = run_cli("pregate", "digest", "--input", str(input_path), cwd=root)
    assert result.returncode == 0, result.stderr
    assert _json(result) == {"subject_digest": compute_subject_digest(payload)}


@pytest.mark.parametrize("input_kind", ["missing", "directory"], ids=str)
def test_pregate_digest_rejects_missing_or_directory_input(run_cli, tmp_path, input_kind):
    root = tmp_path
    if input_kind == "missing":
        input_path = root / "missing-pregate.json"
    else:
        input_path = root / "pregate-dir"
        input_path.mkdir()

    result = run_cli("pregate", "digest", "--input", str(input_path), cwd=root)

    assert result.returncode == 2
    assert "ERROR:" in result.stderr


def test_pregate_check_returns_miss_without_record(state_dir, run_cli):
    root = state_dir.parent
    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "421",
            "--subject-digest",
            "sha256:" + "2" * 64,
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked == {"status": "miss"}


def test_pregate_check_returns_stale_for_digest_mismatch(state_dir, run_cli):
    root = state_dir.parent
    input_path = root / "pregate.json"
    input_path.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "3" * 64, age_hours=3),
        encoding="utf-8",
    )
    run_cli("pregate", "record", "--issue-ref", "421", "--input", str(input_path), cwd=root, check=True)

    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "421",
            "--subject-digest",
            "sha256:" + "4" * 64,
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked["status"] == "stale"


@pytest.mark.parametrize(
    ("age_hours", "expected_status"),
    [
        (71, "hit"),
        (73, "stale"),
    ],
    ids=["ttl_minus_1h_fresh", "ttl_plus_1h_stale"],
)
def test_pregate_check_respects_ttl_boundary(state_dir, run_cli, age_hours, expected_status):
    root = state_dir.parent
    input_path = root / "pregate.json"
    input_path.write_text(
        _record_payload(
            issue_ref="421",
            subject_digest="sha256:" + "5" * 64,
            age_hours=age_hours,
        ),
        encoding="utf-8",
    )
    run_cli("pregate", "record", "--issue-ref", "421", "--input", str(input_path), cwd=root, check=True)

    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "421",
            "--subject-digest",
            "sha256:" + "5" * 64,
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked["status"] == expected_status


def test_pregate_record_overwrites_same_issue_ref(state_dir, run_cli):
    root = state_dir.parent
    first = root / "first.json"
    second = root / "second.json"
    first.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "6" * 64, gate_id="gate-a", age_hours=24),
        encoding="utf-8",
    )
    second.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "7" * 64, gate_id="gate-b", age_hours=1),
        encoding="utf-8",
    )

    first_out = _json(run_cli("pregate", "record", "--issue-ref", "421", "--input", str(first), cwd=root, check=True))
    second_out = _json(run_cli("pregate", "record", "--issue-ref", "421", "--input", str(second), cwd=root, check=True))

    assert first_out["path"] == second_out["path"]
    checked = _json(
        run_cli(
            "pregate",
            "check",
            "--issue-ref",
            "421",
            "--subject-digest",
            "sha256:" + "7" * 64,
            cwd=root,
            check=True,
            env_extra=_now_env(),
        )
    )
    assert checked["record"]["gate_id"] == "gate-b"


def test_pregate_check_ignores_corrupt_cache(state_dir, run_cli):
    root = state_dir.parent
    cache = _cache_dir(root)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "421.json").write_text("{broken", encoding="utf-8")

    checked = _json(run_cli("pregate", "check", "--issue-ref", "421", "--subject-digest", "sha256:" + "8" * 64, cwd=root, check=True))
    assert checked == {"status": "miss"}


def test_init_records_fresh_pregate_reference(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    input_path = root / "pregate.json"
    input_path.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "9" * 64, age_hours=1),
        encoding="utf-8",
    )

    run_cli("pregate", "record", "--issue-ref", "421", "--input", str(input_path), cwd=root, check=True)
    run_cli(
        "init",
        "issue 421 mission",
        "--complexity",
        "Standard",
        "--issue-ref",
        "421",
        cwd=root,
        check=True,
        env_extra=_now_env(),
    )

    state = json.loads((root / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["issue_ref_key"] == "421"
    assert state["pregate"] == {
        "path": str(_cache_dir(root) / "421.json"),
        "subject_digest": "sha256:" + "9" * 64,
        "verdict": "accepted",
        "gate_id": "planning-check",
        "evaluated_at": _iso_utc(_TEST_NOW - timedelta(hours=1)),
    }


def test_init_omits_pregate_reference_when_missing(run_cli, tmp_path):
    root = tmp_path
    run_cli("init", "issue 421 mission", "--complexity", "Standard", "--issue-ref", "421", cwd=root, check=True)

    state = json.loads((root / ".mission-state" / "sessions" / "test.json").read_text())
    assert "pregate" not in state


def test_pregate_issue_ref_is_sanitized_for_path(state_dir, run_cli):
    root = state_dir.parent
    input_path = root / "sanitized.json"
    input_path.write_text(_record_payload(issue_ref="../issue/421", subject_digest="sha256:" + "a" * 64), encoding="utf-8")

    recorded = _json(run_cli("pregate", "record", "--issue-ref", "../issue/421", "--input", str(input_path), cwd=root, check=True))
    path = Path(recorded["path"])
    assert path.parent == _cache_dir(root)
    assert path.name == "___issue_421.json"
    assert path.exists()


def test_resume_init_prefers_fresher_pregate_reference(run_cli, tmp_path):
    root = tmp_path
    (root / ".mission-state").mkdir()
    old = root / "old.json"
    old.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "9" * 64, gate_id="gate-old", age_hours=48),
        encoding="utf-8",
    )
    run_cli("pregate", "record", "--issue-ref", "421", "--input", str(old), cwd=root, check=True)
    run_cli(
        "init",
        "issue 421 mission",
        "--complexity",
        "Standard",
        "--issue-ref",
        "421",
        cwd=root,
        check=True,
        env_extra=_now_env(),
    )

    fresh = root / "fresh.json"
    fresh.write_text(
        _record_payload(issue_ref="421", subject_digest="sha256:" + "b" * 64, gate_id="gate-fresh", age_hours=1),
        encoding="utf-8",
    )
    run_cli("pregate", "record", "--issue-ref", "421", "--input", str(fresh), cwd=root, check=True)
    # 同一 mission の再 init (= resume boundary) で新しい評価が反映されること
    run_cli(
        "init",
        "issue 421 mission",
        "--complexity",
        "Standard",
        "--issue-ref",
        "421",
        cwd=root,
        check=True,
        env_extra=_now_env(),
    )

    state = json.loads((root / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["pregate"]["gate_id"] == "gate-fresh"
    assert state["pregate"]["subject_digest"] == "sha256:" + "b" * 64
