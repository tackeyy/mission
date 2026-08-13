"""#422: local evidence handoff sidecar contract."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


def _digest(payload):
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _publish(run_cli, cwd, topic, payload, *, input_path=None, stdin=False):
    if stdin:
        result = subprocess.run(
            [sys.executable, str(MISSION_STATE_PY), "handoff", "publish", "--topic", topic, "--input", "-"],
            cwd=cwd,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("MISSION_")},
                "MISSION_SESSION_ID": "test",
            },
        )
        return result
    source = input_path
    if source is None:
        source = cwd / "payload.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return run_cli(
        "handoff",
        "publish",
        "--topic",
        topic,
        "--input",
        str(source),
        cwd=cwd,
    )


def test_publish_creates_envelope_and_digest(run_cli, state_dir, tmp_path):
    payload = {"b": 2, "a": ["x", {"z": False}]}

    result = _publish(run_cli, state_dir.parent, "issue-422-evidence", payload)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["seq"] == 1
    assert output["payload_digest"] == _digest(payload)
    envelope = json.loads(Path(output["path"]).read_text(encoding="utf-8"))
    assert envelope == {
        "schema": "mission-evidence-handoff/1",
        "topic": "issue-422-evidence",
        "seq": 1,
        "created_at": envelope["created_at"],
        "producer_session": "test",
        "payload_digest": _digest(payload),
        "payload": payload,
    }


def test_publish_stdin_input(state_dir):
    payload = {"hello": "world"}
    result = subprocess.run(
        [sys.executable, str(MISSION_STATE_PY), "handoff", "publish", "--topic", "issue-422-stdin", "--input", "-"],
        cwd=state_dir.parent,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("MISSION_")},
            "MISSION_SESSION_ID": "test",
        },
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["payload_digest"] == _digest(payload)


def test_await_returns_new_evidence_after_seq(run_cli, state_dir):
    topic = "issue-422-after-seq"
    first = _publish(run_cli, state_dir.parent, topic, {"seq": 1})
    assert first.returncode == 0, first.stderr

    failures = []

    def _publish_next():
        try:
            time.sleep(0.2)
            outcome = _publish(run_cli, state_dir.parent, topic, {"seq": 2})
            if outcome.returncode != 0:
                failures.append(outcome.stderr)
        except BaseException as exc:  # pragma: no cover - background thread failure is surfaced below
            failures.append(str(exc))

    thread = threading.Thread(target=_publish_next, daemon=True)
    thread.start()
    result = run_cli("handoff", "await", "--topic", topic, "--after-seq", "1", cwd=state_dir.parent, check=True)
    thread.join(timeout=5)

    assert not failures, failures
    output = json.loads(result.stdout)
    assert output["seq"] == 2
    assert output["payload"] == {"seq": 2}
    assert Path(output["path"]).name.startswith("2-")


def test_await_timeout_exit_code_3(run_cli, tmp_path):
    (tmp_path / ".mission-state" / "sessions").mkdir(parents=True)
    (tmp_path / ".mission-state" / "sessions" / "test.json").write_text("{}", encoding="utf-8")

    result = run_cli("handoff", "await", "--topic", "issue-422-timeout", "--timeout-sec", "0", cwd=tmp_path)

    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "timeout"


def test_await_ignores_tmp_partial_files(run_cli, state_dir):
    topic_dir = state_dir.parent / ".mission-state" / "handoff" / "issue-422-partial"
    topic_dir.mkdir(parents=True)
    (topic_dir / ".tmp-partial.json").write_text(
        json.dumps({
            "schema": "mission-evidence-handoff/1",
            "topic": "issue-422-partial",
            "seq": 1,
            "created_at": "2026-08-13T00:00:00Z",
            "producer_session": "test",
            "payload_digest": "sha256:" + "0" * 64,
            "payload": {"partial": True},
        }),
        encoding="utf-8",
    )

    result = run_cli("handoff", "await", "--topic", "issue-422-partial", "--timeout-sec", "0", cwd=state_dir.parent)

    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "timeout"


def test_verify_digest_match_and_mismatch(run_cli, state_dir):
    payload = {"verify": True}
    publish = _publish(run_cli, state_dir.parent, "issue-422-verify", payload)
    assert publish.returncode == 0, publish.stderr
    path = json.loads(publish.stdout)["path"]

    ok = run_cli("handoff", "verify", "--path", path, cwd=state_dir.parent)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["payload_digest"] == _digest(payload)

    bad = run_cli(
        "handoff",
        "verify",
        "--path",
        path,
        "--expect-digest",
        "sha256:" + "f" * 64,
        cwd=state_dir.parent,
    )
    assert bad.returncode == 2


@pytest.mark.parametrize("topic", ["../escape", "/absolute", "issue-422/escape"])
def test_topic_slug_validation_rejects_traversal(run_cli, state_dir, topic):
    result = run_cli(
        "handoff",
        "publish",
        "--topic",
        topic,
        "--input",
        "-",
        cwd=state_dir.parent,
        env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert result.returncode == 2


def test_publish_requires_mission_state_dir(run_cli, tmp_path):
    result = run_cli(
        "handoff",
        "publish",
        "--topic",
        "issue-422-missing-state",
        "--input",
        "-",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert result.returncode == 2


def test_seq_increments_per_topic(run_cli, state_dir):
    first = _publish(run_cli, state_dir.parent, "issue-422-seq", {"n": 1})
    second = _publish(run_cli, state_dir.parent, "issue-422-seq", {"n": 2})

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["seq"] == 1
    assert json.loads(second.stdout)["seq"] == 2
