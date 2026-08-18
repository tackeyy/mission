"""#311 (F2): session_role と役割別統計セグメント化.

実運用監査 (2026-08-01): sessions の 75% が Checker 役で、iter=0・passes=False・
partial-done が設計どおりの正規出口なのに、役割概念が state に無いため pass-rate
統計が汚染され (passes 31%)、証拠提出完了が「部分完了」として記録されていた。

Contract under test:
1. init --role <implementer|checker|planning|analyze|release> が session_role を保存
   (省略時 implementer、不正値は拒否)
2. HALT_CATEGORIES に evidence-submitted が追加され mark-halt で使える
3. summarize_pass_rate_population が role_counts と implementer 限定 pass rate を
   additive に返す (既存フィールドは不変)
4. 旧 state (session_role なし) は implementer 扱い (後方互換)
"""

import json
import importlib.util
from pathlib import Path

from mission_persistence.authoritative_reader import read_authoritative_snapshot


def _load(name: str, rel: str):
    path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MC = _load("mission_common", "lib/mission_common.py")


def _sessions(tmp_path):
    d = tmp_path / ".mission-state" / "sessions"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def _session_state(tmp_path):
    path = _sessions(tmp_path)[0]
    return read_authoritative_snapshot(
        path, expected_session_id=path.stem
    ).document_copy()


# ===== 1. init --role =====

def test_init_role_checker_stored(run_cli, tmp_path):
    r = run_cli("init", "PR #100 を独立レビュー", "--complexity", "Standard",
                "--role", "checker", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = _session_state(tmp_path)
    assert state["session_role"] == "checker"


def test_init_role_default_implementer(run_cli, tmp_path):
    run_cli("init", "some mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    state = _session_state(tmp_path)
    assert state["session_role"] == "implementer"


def test_init_role_invalid_rejected(run_cli, tmp_path):
    r = run_cli("init", "m", "--complexity", "Standard", "--role", "wizard", cwd=tmp_path)
    assert r.returncode != 0


# ===== 2. evidence-submitted カテゴリ =====

def test_evidence_submitted_in_halt_categories():
    assert "evidence-submitted" in MC.HALT_CATEGORIES


def test_mark_halt_accepts_evidence_submitted(run_cli, tmp_path):
    run_cli("init", "checker review", "--complexity", "Standard",
            "--role", "checker", cwd=tmp_path, check=True)
    r = run_cli("mark-halt", "--reason", "証拠提出完了",
                "--category", "evidence-submitted", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = _session_state(tmp_path)
    assert state["halt_category"] == "evidence-submitted"


# ===== 3. role 別統計 (additive) =====

def _state(role=None, passes=False, halt="", loop_active=False):
    d = {
        "mission_id": "x", "loop_active": loop_active, "passes": passes,
        "halt_reason": halt, "halt_category": "partial-done" if halt else "",
        "started_at": "2026-08-01T00:00:00Z",
        "last_activity_at": "2026-08-01T00:10:00Z",
        "updated_at": "2026-08-01T00:10:00Z",
    }
    if role is not None:
        d["session_role"] = role
    return d


def test_population_role_counts_and_implementer_rate():
    states = [
        _state(role="implementer", passes=True),
        _state(role="implementer", passes=False, halt="halted"),
        _state(role="checker", passes=False, halt="evidence handed off"),
        _state(role="checker", passes=False, halt="evidence handed off"),
        _state(role="planning", passes=False, halt="verdict posted"),
    ]
    result = MC.summarize_pass_rate_population(states, stale_after_sec=10800)
    assert result["role_counts"]["implementer"] == 2
    assert result["role_counts"]["checker"] == 2
    assert result["role_counts"]["planning"] == 1
    assert result["implementer_pass_rate_numerator"] == 1
    assert result["implementer_pass_rate_denominator"] == 2
    assert result["implementer_pass_rate"] == 0.5
    # 既存フィールドは全 role 対象のまま不変
    assert result["raw_pass_rate_denominator"] == 5


def test_population_missing_role_counts_as_implementer():
    states = [_state(role=None, passes=True)]
    result = MC.summarize_pass_rate_population(states, stale_after_sec=10800)
    assert result["role_counts"]["implementer"] == 1
    assert result["implementer_pass_rate"] == 1.0
