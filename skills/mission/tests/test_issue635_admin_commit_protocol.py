"""Issue #635 U5-1: administrative commit protocol と resolve-archive の移行.

ADR-006 点 3: 「protocol なしの administrative writer」を廃する。protocol は
identity 検証つき read / validation / atomic publish / 定義された失敗結果を
最低限提供する。
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest


MISSION_STATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)


def _write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _atomic_writer(calls):
    def write_document(path, document, *, administrative=False):
        assert administrative is True
        calls.append(dict(document))
        path.write_text(json.dumps(document), encoding="utf-8")

    return write_document


def test_capture_record_rejects_symlink(tmp_path):
    from mission_persistence.administrative import (
        AdministrativeCommitError,
        capture_record,
    )

    real = tmp_path / "real.json"
    _write_json(real, {"ok": True})
    link = tmp_path / "link.json"
    link.symlink_to(real)

    with pytest.raises(AdministrativeCommitError) as failure:
        capture_record(link)
    assert failure.value.code == "record-identity-invalid"


def test_capture_record_rejects_hardlinked_record(tmp_path):
    from mission_persistence.administrative import (
        AdministrativeCommitError,
        capture_record,
    )

    real = tmp_path / "real.json"
    _write_json(real, {"ok": True})
    os.link(real, tmp_path / "alias.json")

    with pytest.raises(AdministrativeCommitError) as failure:
        capture_record(real)
    assert failure.value.code == "record-identity-invalid"


@pytest.mark.parametrize(
    "payload", (b"not-json", b"[1,2]", b"\xff\xfe"), ids=("garbage", "array", "binary")
)
def test_capture_record_rejects_non_object_payloads(tmp_path, payload):
    from mission_persistence.administrative import (
        AdministrativeCommitError,
        capture_record,
    )

    target = tmp_path / "record.json"
    target.write_bytes(payload)
    with pytest.raises(AdministrativeCommitError) as failure:
        capture_record(target)
    assert failure.value.code == "record-invalid"


def test_administrative_commit_publishes_validated_mutation(tmp_path):
    from mission_persistence.administrative import administrative_commit

    target = tmp_path / "record.json"
    _write_json(target, {"phase": "halted", "resolution_status": None})
    calls = []

    def validate(document):
        assert document["phase"] == "halted"

    def mutate(document):
        document["resolution_status"] = "resolved"

    captured, proposed = administrative_commit(
        target, validate=validate, mutate=mutate, write_document=_atomic_writer(calls)
    )

    assert proposed["resolution_status"] == "resolved"
    assert calls == [proposed]
    assert json.loads(captured.payload)["resolution_status"] is None
    assert json.loads(target.read_text())["resolution_status"] == "resolved"


def test_administrative_commit_validation_failure_writes_nothing(tmp_path):
    from mission_persistence.administrative import administrative_commit

    target = tmp_path / "record.json"
    _write_json(target, {"phase": "planning"})
    before = target.read_bytes()
    calls = []

    def validate(document):
        raise ValueError("record is still active")

    with pytest.raises(ValueError):
        administrative_commit(
            target,
            validate=validate,
            mutate=lambda document: None,
            write_document=_atomic_writer(calls),
        )
    assert calls == []
    assert target.read_bytes() == before


def test_administrative_commit_rejects_concurrent_record_change(tmp_path):
    from mission_persistence.administrative import (
        AdministrativeCommitError,
        administrative_commit,
    )

    target = tmp_path / "record.json"
    _write_json(target, {"phase": "halted"})
    calls = []

    def mutate(document):
        # capture と publish の間に別 writer が動いた状況を再現する
        _write_json(target, {"phase": "halted", "intruder": True})
        document["resolution_status"] = "resolved"

    with pytest.raises(AdministrativeCommitError) as failure:
        administrative_commit(
            target,
            validate=lambda document: None,
            mutate=mutate,
            write_document=_atomic_writer(calls),
        )
    assert failure.value.code == "record-changed"
    assert calls == []
    assert json.loads(target.read_text()) == {"phase": "halted", "intruder": True}


def test_restore_record_restores_bytes_or_fails_closed(tmp_path):
    from mission_persistence.administrative import (
        AdministrativeCommitError,
        capture_record,
        restore_record,
    )

    target = tmp_path / "record.json"
    _write_json(target, {"phase": "halted"})
    captured = capture_record(target)
    _write_json(target, {"phase": "halted", "resolution_status": "resolved"})

    def write_bytes(path, content):
        path.write_bytes(content)

    restore_record(captured, write_bytes)
    assert target.read_bytes() == captured.payload

    def failing(path, content):
        raise OSError("disk full")

    with pytest.raises(AdministrativeCommitError) as failure:
        restore_record(captured, failing)
    assert failure.value.code == "rollback-failed"


def _atomic_write_callers() -> dict[str, set[str]]:
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    callers: dict[str, set[str]] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in {"atomic_write_json", "atomic_write_bytes"} and self.stack:
                callers.setdefault(self.stack[0], set()).add(name)
            self.generic_visit(node)

    Visitor().visit(tree)
    return callers


# 直接 atomic_write_* を呼んでよい writer の閉リスト (U5-1 棚卸し)。
# 追加は administrative commit protocol (mission_persistence.administrative)
# を通すか、U5 の設計審査を経ること。
# 既知除外: _legacy_lifecycle_repository.write_state / _add_to_aggregate 系の
# aggregate index 更新は U5-2 (#636) で復旧設計へ移行するまで残置。
ALLOWED_DIRECT_ATOMIC_WRITERS = {
    "_add_to_aggregate",
    "_add_to_aggregate_strict",
    "_atomic_write_archive_pointer",
    "_build_worktree_archive_staging",
    "_commit_specialist_state_with_archive",
    "_initialize_legacy_v4",
    "_legacy_evidence_repository",
    "_legacy_lifecycle_repository",
    "_publish_preflight_pointer_transaction",
    "_publish_state_archive_compaction",
    "_remove_from_aggregate",
    "_remove_from_aggregate_strict",
    "backup_state",
    "cmd_specialists_consent",
    "cmd_verify_provider_approval",
}


def test_direct_atomic_writers_are_a_closed_inventory():
    callers = set(_atomic_write_callers())
    assert "cmd_resolve_archive" not in callers, (
        "resolve-archive は administrative commit protocol 経由で書くこと"
    )
    assert callers == ALLOWED_DIRECT_ATOMIC_WRITERS


def _write_halted_record(path: Path, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mission": "resolve archive fixture",
                "mission_id": "abc123456789",
                "complexity": "Standard",
                "iteration": 1,
                "threshold": 4.0,
                "score_history": [],
                "loop_active": False,
                "passes": False,
                "halt_reason": "threshold gate remains unmet after max iterations",
                "halt_category": "stagnation",
                "started_at": "2026-06-18T00:00:00Z",
                "updated_at": "2026-06-18T00:10:00Z",
                "project_root": str(path.parents[2]),
                "session_id": session_id,
                "agent": "codex",
                "schema_version": 2,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_resolve_archive_still_resolves_terminal_records(legacy_run_cli, tmp_path):
    """E2E 回帰: protocol 経由でも resolve-archive の挙動は不変。"""
    target = tmp_path / ".mission-state" / "sessions" / "resolved-session.json"
    _write_halted_record(target, "resolved-session")

    result = legacy_run_cli(
        "resolve-archive", "--path", str(target),
        "--status", "resolved", "--note", "fixture resolution",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(target.read_text())
    assert document["resolution_status"] == "resolved"
    assert document["resolution_note"] == "fixture resolution"
    assert document["halt_category"] == "stagnation"


def test_resolve_archive_rejects_hardlinked_target(legacy_run_cli, tmp_path):
    """TOCTOU 対策: lock 内の identity 検証が hardlink された record を拒否する。"""
    target = tmp_path / ".mission-state" / "sessions" / "linked-session.json"
    _write_halted_record(target, "linked-session")
    os.link(target, tmp_path / ".mission-state" / "sessions" / "alias.json")
    before = target.read_bytes()

    result = legacy_run_cli(
        "resolve-archive", "--path", str(target),
        "--status", "resolved",
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "record-identity-invalid" in result.stderr
    assert target.read_bytes() == before
