"""Issue #175: task_profile.risk=high 付与基準のキャリブレーション.

HIGH_RISK_KEYWORDS を Issue #174 と同一ポリシーで較正:
- "prod" を除外 ("production" が既にあり冗長。"product" / "productivity" への誤発火源)
- "auth" → "authenticat", "authoriz", "oauth" に置換
- "token" → "api token", "api-token", "api_key", "access token", "access-token", "bearer" に置換

注意: classify_task_profile は PROFILE_KEYWORDS にヒットしない場合 risk="low" を返す。
HIGH_RISK_KEYWORDS は PROFILE_KEYWORDS ヒット後の risk=high 昇格条件。
このため真陽性テストは profile keyword を含む mission text を用いる。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

_module = None


def _get_module():
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location("mission_state_175", MISSION_STATE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module = mod
    return _module


# ============================================================
# False-positive 回帰テスト (誤検知ゼロを保証)
# ============================================================


@pytest.mark.parametrize("mission,description", [
    (
        "product roadmap を改善する",
        "'prod' が 'product' に部分一致して誤発火しない (product は PROFILE_KEYWORDS 'product' にヒット → risk 評価到達)",
    ),
    (
        "awaiting external authority",
        "'auth' が 'authority' に部分一致して誤発火しない",
    ),
    (
        "acme-token-api の backend 実装",
        "プロダクト名に 'token' が含まれても複合語でなければ誤発火しない (backend が PROFILE_KEYWORDS にヒット → risk 評価到達)",
    ),
])
def test_risk_high_false_positive_regression(mission, description):
    """Issue #175: 較正後は prod/auth/token 単体の誤発火がない."""
    mod = _get_module()
    result = mod.classify_task_profile(mission)
    assert result["risk"] != "high", (
        f"False positive: {description}\n"
        f"  mission={mission!r}\n"
        f"  risk={result['risk']!r}"
    )


@pytest.mark.parametrize("mission,description", [
    (
        "プロダクトの productivity 向上",
        "'prod' が 'productivity' に部分一致しても PROFILE_KEYWORDS ヒットなし → risk=low (not high)",
    ),
])
def test_risk_not_high_no_profile_match(mission, description):
    """Issue #175: PROFILE_KEYWORDS ヒットがなければ risk は high にならない."""
    mod = _get_module()
    result = mod.classify_task_profile(mission)
    assert result["risk"] != "high", (
        f"Unexpected high risk: {description}\n"
        f"  mission={mission!r}\n"
        f"  risk={result['risk']!r}"
    )


# ============================================================
# True-positive テスト (真陽性は引き続き risk=high)
# NOTE: PROFILE_KEYWORDS にヒットする mission text を使い、risk 評価が到達する状態で検証する
# ============================================================


@pytest.mark.parametrize("mission,description", [
    (
        "production deploy を実施する",
        "'production' + 'deploy' はいずれも HIGH_RISK_KEYWORDS。deploy は infra PROFILE_KEYWORDS にもヒット",
    ),
    (
        "api token をローテーションする",
        "複合語 'api token' は HIGH_RISK_KEYWORDS。api は backend PROFILE_KEYWORDS にヒット",
    ),
    (
        "OAuth 実装で認証フローを修正する",
        "'oauth' は HIGH_RISK_KEYWORDS。security PROFILE_KEYWORDS にもヒット",
    ),
    (
        "payment api の決済処理実装",
        "'payment' は HIGH_RISK_KEYWORDS。api が backend PROFILE_KEYWORDS にヒットし risk 評価到達",
    ),
    (
        "drop table の migration を安全に実行する",
        "'drop table' は HIGH_RISK_KEYWORDS。migration が database PROFILE_KEYWORDS にヒット",
    ),
    (
        "bearer token による api 認証",
        "'bearer' は HIGH_RISK_KEYWORDS。api が backend PROFILE_KEYWORDS にヒット",
    ),
    (
        "access-token の有効期限を更新する api",
        "'access-token' は HIGH_RISK_KEYWORDS。api が backend PROFILE_KEYWORDS にヒット",
    ),
    (
        "authentication フローを security で修正する",
        "'authenticat' 語幹にマッチ。security が PROFILE_KEYWORDS にヒット",
    ),
    (
        "authorization ヘッダの検証 api",
        "'authoriz' 語幹にマッチ。api が backend PROFILE_KEYWORDS にヒット",
    ),
    (
        "pii データのマスキング処理",
        "'pii' は HIGH_RISK_KEYWORDS かつ security PROFILE_KEYWORDS にヒット",
    ),
    (
        "secret key のローテーション",
        "'secret' は HIGH_RISK_KEYWORDS かつ security PROFILE_KEYWORDS にヒット",
    ),
    (
        "security audit の実施",
        "'security' は HIGH_RISK_KEYWORDS かつ security PROFILE_KEYWORDS にヒット",
    ),
    (
        "irreversible な変更を security 審査する",
        "'irreversible' は HIGH_RISK_KEYWORDS。security が PROFILE_KEYWORDS にヒット",
    ),
    (
        "delete data from the database",
        "'delete data' は HIGH_RISK_KEYWORDS。database が database PROFILE_KEYWORDS にヒット",
    ),
])
def test_risk_high_true_positive(mission, description):
    """Issue #175: 較正後も真陽性ケースは risk=high を維持する."""
    mod = _get_module()
    result = mod.classify_task_profile(mission)
    assert result["risk"] == "high", (
        f"False negative: {description}\n"
        f"  mission={mission!r}\n"
        f"  risk={result['risk']!r}"
    )
