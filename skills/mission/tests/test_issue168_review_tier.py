"""Issue #168: mission-state.py に review_tier の導出・保存・CLI 対応・reviewer_count 連動を実装.

テスト設計:
  - derive_review_tier() 純関数の全パターン (ベーステーブル, エスカレータ, 日本語キーワード)
  - init: auto 導出 (--review-tier 未指定) と user 指定 (--review-tier <val>) の両パス
  - set: review_tier= の WARNING / 無効値 exit 2
  - set complexity= 変更時の auto 再導出 vs user 値維持
  - 後方互換: review_tier フィールドを持たない既存 state で set/next が動く
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# --- in-process import of derive_review_tier (pure function) ---

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

_module = None


def _get_module():
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location("mission_state", MISSION_STATE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module = mod
    return _module


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


# ============================================================
# derive_review_tier() 純関数テスト
# ============================================================


def test_derive_review_tier_simple_gives_light():
    """Simple complexity → light."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Simple")
    assert tier == "light"
    assert signals == []


def test_derive_review_tier_standard_gives_standard():
    """Standard complexity → standard."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Standard")
    assert tier == "standard"
    assert signals == []


def test_derive_review_tier_complex_gives_full():
    """Complex complexity → full."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Complex")
    assert tier == "full"
    assert signals == []


def test_derive_review_tier_critical_gives_full():
    """Critical complexity → full."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Critical")
    assert tier == "full"
    assert signals == []


def test_derive_review_tier_none_complexity_gives_standard():
    """complexity=None → standard (安全側フォールバック)."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", None)
    assert tier == "standard"
    assert signals == []


def test_derive_review_tier_unknown_complexity_gives_standard():
    """complexity='Unknown' → standard."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Unknown")
    assert tier == "standard"
    assert signals == []


def test_derive_review_tier_unrecognized_complexity_gives_standard():
    """未知の complexity 文字列 → standard."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Huge")
    assert tier == "standard"
    assert signals == []


def test_derive_review_tier_task_profile_risk_high_escalates_to_full():
    """task_profile_risk='high' は Simple でも full に昇格."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("simple task", "Simple", task_profile_risk="high")
    assert tier == "full"
    assert "task_profile.risk=high" in signals


def test_derive_review_tier_task_profile_risk_not_high_no_escalation():
    """task_profile_risk が high でなければ昇格しない."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("some mission", "Simple", task_profile_risk="medium")
    assert tier == "light"
    assert not any("task_profile.risk" in s for s in signals)


# --- 不可逆系英語キーワード ---

@pytest.mark.parametrize("kw", [
    "deploy", "release", "migration", "drop", "delete",
    "publish", "production", "push", "merge",
])
def test_derive_review_tier_irreversible_english_keyword_escalates(kw):
    """不可逆系英語キーワードが本文に含まれると full に昇格."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier(f"run {kw} pipeline", "Simple")
    assert tier == "full", f"kw={kw!r} should escalate to full"
    assert any("irreversible-keyword" in s for s in signals)


@pytest.mark.parametrize("kw", ["Deploy", "DEPLOY", "Release"])
def test_derive_review_tier_irreversible_english_keyword_case_insensitive(kw):
    """不可逆系英語キーワードは大文字小文字を区別しない."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier(f"{kw} something", "Simple")
    assert tier == "full"


# --- 不可逆系日本語キーワード ---

@pytest.mark.parametrize("kw", [
    "本番", "リリース", "マイグレーション", "削除", "公開", "決済",
])
def test_derive_review_tier_irreversible_japanese_keyword_escalates(kw):
    """不可逆系日本語キーワードが含まれると full に昇格."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier(f"{kw}対応", "Simple")
    assert tier == "full", f"kw={kw!r} should escalate to full"
    assert any("irreversible-keyword" in s for s in signals)


# --- セキュリティ系英語キーワード ---

@pytest.mark.parametrize("kw", [
    "auth", "secret", "token", "credential", "password",
])
def test_derive_review_tier_security_english_keyword_escalates(kw):
    """セキュリティ系英語キーワードが含まれると full に昇格."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier(f"update {kw} system", "Standard")
    assert tier == "full", f"kw={kw!r} should escalate to full"
    assert any("security-keyword" in s for s in signals)


# --- セキュリティ系日本語キーワード ---

@pytest.mark.parametrize("kw", ["認証", "秘密", "鍵"])
def test_derive_review_tier_security_japanese_keyword_escalates(kw):
    """セキュリティ系日本語キーワードが含まれると full に昇格."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier(f"{kw}の管理", "Standard")
    assert tier == "full", f"kw={kw!r} should escalate to full"
    assert any("security-keyword" in s for s in signals)


def test_derive_review_tier_signal_contains_hit_keyword():
    """signals に実際にヒットしたキーワードが記録される."""
    mod = _get_module()
    _, signals = mod.derive_review_tier("deploy to production", "Standard")
    # deploy か production のどちらかが signals に現れる
    assert any("deploy" in s or "production" in s for s in signals)


def test_derive_review_tier_no_escalation_no_signals():
    """エスカレータに該当しない場合は signals が空."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("refactor the codebase", "Standard")
    assert signals == []


def test_derive_review_tier_escalation_does_not_downgrade():
    """full ベースのケースでは下がらない (降格ロジックがないことを確認)."""
    mod = _get_module()
    tier, signals = mod.derive_review_tier("simple refactor", "Critical", task_profile_risk="low")
    assert tier == "full"  # Critical のベースが full なので変わらない


# ============================================================
# cmd_init テスト (subprocess)
# ============================================================


def test_init_without_review_tier_auto_derives(run_cli, tmp_path):
    """--review-tier 未指定時は auto 導出で state に review_tier* が保存される."""
    run_cli("init", "refactor mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "standard"
    assert s["review_tier_source"] == "auto"
    assert isinstance(s["review_tier_signals"], list)


def test_init_auto_review_tier_simple(run_cli, tmp_path):
    """Simple complexity → auto で light."""
    run_cli("init", "simple cleanup", "--complexity", "Simple", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "light"
    assert s["review_tier_source"] == "auto"


def test_init_auto_review_tier_complex(run_cli, tmp_path):
    """Complex complexity → auto で full."""
    run_cli("init", "complex refactor", "--complexity", "Complex", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "auto"


def test_init_user_specified_review_tier_light(run_cli, tmp_path):
    """--review-tier light を明示すると source=user で保存."""
    run_cli("init", "quick fix", "--complexity", "Standard", "--review-tier", "light",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "light"
    assert s["review_tier_source"] == "user"
    assert s["review_tier_signals"] == []


def test_init_user_specified_review_tier_full(run_cli, tmp_path):
    """--review-tier full を明示すると source=user で保存."""
    run_cli("init", "critical fix", "--complexity", "Simple", "--review-tier", "full",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "user"


def test_init_review_tier_sets_reviewer_count(run_cli, tmp_path):
    """review_tier から reviewer_count が TIER_REVIEWER_COUNT で設定される."""
    run_cli("init", "standard task", "--complexity", "Standard", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 2  # standard → 2


def test_init_review_tier_light_reviewer_count_1(run_cli, tmp_path):
    """light → reviewer_count=1."""
    run_cli("init", "simple task", "--complexity", "Simple", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 1


def test_init_review_tier_full_reviewer_count_3(run_cli, tmp_path):
    """full → reviewer_count=3."""
    run_cli("init", "critical task", "--complexity", "Critical", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 3


def test_init_mission_with_deploy_keyword_escalates_to_full(run_cli, tmp_path):
    """ミッション記述に deploy キーワードがある場合 Simple でも full に昇格."""
    run_cli("init", "deploy to production environment", "--complexity", "Simple",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "auto"
    assert any("irreversible-keyword" in sig for sig in s["review_tier_signals"])


def test_init_unknown_complexity_auto_derives_standard(run_cli, tmp_path):
    """complexity 未指定 (Unknown) でも review_tier=standard が auto 導出される."""
    r = run_cli("init", "some mission", cwd=tmp_path)
    assert r.returncode == 0
    s = _read(tmp_path)
    assert s["review_tier"] == "standard"
    assert s["review_tier_source"] == "auto"


# ============================================================
# cmd_set テスト (subprocess)
# ============================================================


def test_set_review_tier_user_overrides(run_cli, tmp_path):
    """set review_tier=standard で source=user に更新される."""
    run_cli("init", "some mission", "--complexity", "Simple", cwd=tmp_path, check=True)
    run_cli("set", "review_tier=standard", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "standard"
    assert s["review_tier_source"] == "user"


def test_set_review_tier_syncs_reviewer_count(run_cli, tmp_path):
    """set review_tier= 時に reviewer_count が TIER_REVIEWER_COUNT で同期される."""
    run_cli("init", "some mission", "--complexity", "Simple", cwd=tmp_path, check=True)
    run_cli("set", "review_tier=full", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 3


def test_set_review_tier_explicit_reviewer_count_wins(run_cli, tmp_path):
    """reviewer_count を同時に明示した場合はそちらが優先."""
    run_cli("init", "some mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    run_cli("set", "review_tier=full", "reviewer_count=4", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 4


def test_set_review_tier_invalid_value_exits_2(run_cli, tmp_path):
    """review_tier に無効値を指定すると exit 2."""
    run_cli("init", "some mission", cwd=tmp_path, check=True)
    r = run_cli("set", "review_tier=extreme", cwd=tmp_path)
    assert r.returncode == 2


def test_set_review_tier_warning_when_below_derived(run_cli, tmp_path):
    """auto 導出より低い tier を user 指定すると stderr に WARNING が出る (拒否はしない)."""
    # deploy キーワードで full が auto 導出される
    run_cli("init", "deploy the service", "--complexity", "Standard",
            cwd=tmp_path, check=True)
    r = run_cli("set", "review_tier=light", cwd=tmp_path)
    assert r.returncode == 0, f"should succeed: stderr={r.stderr}"
    assert "warning" in r.stderr.lower() or "WARNING" in r.stderr


def test_set_review_tier_no_warning_when_at_or_above_derived(run_cli, tmp_path):
    """auto 導出以上の tier を指定しても WARNING は出ない."""
    run_cli("init", "simple task", "--complexity", "Simple", cwd=tmp_path, check=True)
    # Simple → light、full は light より上なので WARNING なし
    r = run_cli("set", "review_tier=full", cwd=tmp_path)
    assert r.returncode == 0
    assert "WARNING" not in r.stderr


def test_set_complexity_auto_source_rederives_review_tier(run_cli, tmp_path):
    """review_tier_source=auto 時に complexity 変更で tier が再導出される."""
    run_cli("init", "some mission", "--complexity", "Simple", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "light"
    assert s["review_tier_source"] == "auto"

    run_cli("set", "complexity=Complex", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "auto"
    assert s["reviewer_count"] == 3


def test_set_complexity_user_source_preserves_review_tier(run_cli, tmp_path):
    """review_tier_source=user 時は complexity 変更で tier を再導出しない."""
    run_cli("init", "some mission", "--complexity", "Simple", "--review-tier", "full",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "user"

    run_cli("set", "complexity=Standard", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    # user 指定なので tier は変わらない
    assert s["review_tier"] == "full"
    assert s["review_tier_source"] == "user"
    # reviewer_count も tier 由来の 3 を維持 (complexity 由来の 2 に変わらない)
    assert s["reviewer_count"] == 3


def test_set_complexity_source_field_absent_treated_as_auto(run_cli, tmp_path, state_dir):
    """review_tier_source フィールドが存在しない旧 state では complexity 変更で再導出する."""
    # state_dir fixture が作る初期 state は review_tier* を持たない
    r = run_cli("set", "complexity=Complex", cwd=state_dir.parent)
    assert r.returncode == 0
    s = json.loads((state_dir / "sessions" / "test.json").read_text())
    # 再導出で full になる
    assert s.get("review_tier") == "full"


# ============================================================
# 後方互換テスト
# ============================================================


def test_backward_compat_state_without_review_tier_set(run_cli, state_dir):
    """review_tier を持たない既存 state で set が KeyError を起こさない."""
    r = run_cli("set", "iteration=2", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr={r.stderr}"


def test_backward_compat_state_without_review_tier_next(run_cli, state_dir):
    """review_tier を持たない既存 state で next が KeyError を起こさない."""
    r = run_cli("next", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr={r.stderr}"


def test_backward_compat_state_without_review_tier_get(run_cli, state_dir):
    """review_tier を持たない既存 state で get が動く."""
    r = run_cli("get", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr={r.stderr}"
