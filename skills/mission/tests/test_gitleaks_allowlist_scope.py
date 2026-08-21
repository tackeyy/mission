"""gitleaks allowlist の抑制範囲ガード (RED→GREEN).

`.gitleaks.toml` の allowlist は、誤検知を消すために「値の形」で抑制する。ここが
緩むと本物の credential まで黙って抑制され、**検出ゼロが無意味になる**。

実際に一度緩かった: `^[a-z][a-z0-9_]{2,48}$` は 32 桁 hex・40 桁 hex・小文字 Base32
のいずれにも該当し、MD5 / SHA1 / TOTP シークレットの形をそのまま通していた。
「lowercase snake_case にしか当たらない」という思い込みが誤りだった。

gitleaks は CI に入っていないため、設定変更の正しさを CI で担保する手段がない。
そこで**正規表現そのものを Python 側で固定する**。gitleaks バイナリを必要とせず、
「抑制してよい形」と「抑制してはいけない形」の境界を回帰テストにする。
"""
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / ".gitleaks.toml"


def _secret_scoped_allowlists():
    """`regexTarget = "secret"` を持つ allowlist を返す。

    TOML は tomllib で読む。設定を自前の正規表現で切り出すと、書式のゆれ (改行位置・
    引用符の種類) で黙って 0 件になり、テストが「合格しているのに何も見ていない」状態
    に落ちる。実際に一度そうなった。
    """
    with CONFIG.open("rb") as fh:
        config = tomllib.load(fh)
    return [
        entry
        for entry in config.get("allowlists", [])
        if entry.get("regexTarget") == "secret"
    ]


def _allowlist_secret_regexes():
    return [r for entry in _secret_scoped_allowlists() for r in entry.get("regexes", [])]


def _allowlist_paths():
    return [p for entry in _secret_scoped_allowlists() for p in entry.get("paths", [])]


# 抑制されてよい値: benchmark answer key の指標識別子。
ALLOWED = [
    "p95_improvement_factor",
    "p95_improved_every_week",
    "active_user_growth_pct",
    "avg_weekly_infra_cost_usd",
    "cause_nightly_reindex_lock_contention",
]

# 抑制されてはいけない値: credential の形。いずれも小文字のみで構成されうるため、
# 「小文字なら安全」という単純な条件では弾けない。
FORBIDDEN = [
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",              # 32 桁 hex (MD5 / UUID から - を除いた形)
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6a7b8c9d0",      # 40 桁 hex (SHA1)
    "a0b1c2d3e4f5a6b7",                              # 16 桁 hex
    "abcdefghijklmnopqrstuvwxyz234567",              # 小文字 Base32 (TOTP シークレット等)
    "deadbeefcafebabedeadbeefcafebabe",              # hex 語
]


def test_config_declares_a_secret_scoped_allowlist():
    """前提が消えたらこのテスト群は無意味になる。存在自体を固定する。"""
    assert _allowlist_secret_regexes(), (
        "regexTarget = \"secret\" を持つ allowlist が消えている。"
        "抑制が値の形で絞られなくなった可能性がある"
    )


def test_allowlist_matches_the_metric_identifiers_it_exists_for():
    regexes = [re.compile(r) for r in _allowlist_secret_regexes()]
    for value in ALLOWED:
        assert any(r.match(value) for r in regexes), (
            f"抑制対象のはずの識別子が外れている: {value}"
        )


def test_allowlist_does_not_match_credential_shapes():
    """本テストの核心。ここが落ちたら抑制が広がっている。"""
    regexes = [re.compile(r) for r in _allowlist_secret_regexes()]
    leaked = [v for v in FORBIDDEN if any(r.match(v) for r in regexes)]
    assert not leaked, (
        "credential の形が allowlist に該当している。gitleaks がこれらを黙って"
        f"抑制するようになる:\n  " + "\n  ".join(leaked)
    )


def test_allowlist_paths_are_anchored_at_the_start():
    """未アンカーだと別ディレクトリにも一致する。

    `benchmarks/...` は `not-benchmarks/...` の部分文字列でもあるため、`^` がないと
    無関係なパスまで抑制対象になる。
    """
    for path_re in _allowlist_paths():
        assert path_re.startswith("^"), (
            f"paths が先頭固定されていない: {path_re}\n"
            "未アンカーだと接頭辞違いのディレクトリにも一致する"
        )
        compiled = re.compile(path_re)
        assert not compiled.search("not-benchmarks/mission-vs-goal/answer-keys/x.json"), (
            f"接頭辞違いのパスに一致する: {path_re}"
        )


def test_allowlist_paths_do_not_span_directories():
    """`.*` を挟むと想定外の深さのパスまで拾う。"""
    for path_re in _allowlist_paths():
        compiled = re.compile(path_re)
        assert not compiled.search(
            "benchmarks/mission-vs-goal/answer-keys/nested/deeper/secret.json"
        ), f"想定より深い階層に一致する: {path_re}"
