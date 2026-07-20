"""固有名詞の再混入ガード (RED→GREEN).

本リポジトリは PUBLIC。設計の説明は一般概念語 (ontology / object / property /
link / action / lineage / provenance / branch / scenario / finding / score /
audit 等) で行い、特定ベンダーの製品名・造語を持ち込まない方針。

一度手作業で除去しても、監査ログ・ベンチ成果物・実行ログのような「後から生成
される派生物」が再混入させるため、tracked ファイル全体を毎 PR で機械検査する。

禁止語は **平文で持たない**。リスト自体が「何を伏せているか」の開示になるため、
sha256 の先頭 16 桁で照合する。語を追加するには:

    python3 -c "import hashlib;print(hashlib.sha256(b'<語(小文字)>').hexdigest()[:16])"

を実行し、出力を _BANNED_HASHES に足す。
"""
import hashlib
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_BANNED_HASHES = frozenset({
    "c21b962e71f1969d",
    "dfb316701857783d",
    "0f483258e21eb2cf",
    "e5af78f00ddc515a",
    "6b905c825ebff86a",
})

# 禁止語を部分に含むが、無関係で正当な複合語。ここを許可しておかないと偽陽性が
# 大量に出て、本物の検出がノイズに埋没する (実測: 自プロジェクト名で 25 件)。
_ALLOWED_COMPOUND_HASHES = frozenset({
    "9a345efb9475bea5",  # 自プロジェクト名
})

# ASCII 英数字・アンダースコア・ハイフンの連なりを 1 チャンクとして切り出す。
# 日本語との境界を \b / \w に頼らないのが要点: Python の \w は Unicode 単語文字を
# 含むため、禁止語が日本語に直結した形 ("設計を<語>モデルで") を取りこぼす。
_CHUNK_RE = re.compile(r"[A-Za-z0-9_-]+")

_EXEMPT = {"skills/mission/tests/test_vendor_fingerprint.py"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip", ".woff", ".woff2"}


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _banned_tokens(line, banned=_BANNED_HASHES):
    """行から禁止語トークンを拾う。見つからなければ空リスト。"""
    found = []
    for chunk in _CHUNK_RE.findall(line):
        # 区切り文字入りの識別子 (mission_<語>_redesign 等) も分解して照合する。
        # 実行ログ経由で再混入したのはこの形。
        parts = [p for p in re.split(r"[-_]+", chunk.lower()) if p]
        for i, part in enumerate(parts):
            if _h(part) not in banned:
                continue
            # 隣接語と連結すると正当な複合語になる場合は見逃す
            # (自プロジェクト名を含む <許可語>-email-hook-release のような派生も拾う)
            neighbours = []
            if i > 0:
                neighbours.append(f"{parts[i - 1]}-{part}")
            if i + 1 < len(parts):
                neighbours.append(f"{part}-{parts[i + 1]}")
            if any(_h(n) in _ALLOWED_COMPOUND_HASHES for n in neighbours):
                continue
            found.append(part)
    return found


def _tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


# ---- 照合ロジックの単体テスト (実際の禁止語は使わず合成語で検証する) ----
_FIXTURE = frozenset({"73b5a32453860402"})  # sha256('zzsynthetic')[:16]


def test_detects_standalone_token():
    assert _banned_tokens("zzsynthetic の思想で再設計", _FIXTURE)


def test_detects_token_adjacent_to_japanese():
    """\\b / \\w は Unicode 単語文字を含むため、日本語直結で取りこぼしやすい。"""
    assert _banned_tokens("設計をzzsyntheticモデルで完成", _FIXTURE)


def test_detects_token_inside_separated_identifier():
    """実行ログ由来のファイル名で再混入した実例の形。"""
    assert _banned_tokens("2026-06-18T06-21-35-Ztry-mission_zzsynthetic_redesign.md", _FIXTURE)
    assert _banned_tokens("docs/zzsynthetic-redesign-analysis.md", _FIXTURE)


def test_scan_covers_file_paths_not_only_contents():
    """過去の実混入はファイル名だった。パス照合が外れると素通りする。"""
    assert _banned_tokens("docs/zzsynthetic-redesign-analysis.md", _FIXTURE)
    assert not _banned_tokens("docs/ontology-redesign-analysis.md", _FIXTURE)


def test_ignores_substrings():
    for sample in ("zzsyntheticality", "prezzsynthetic", "zzsynthetics"):
        assert not _banned_tokens(sample, _FIXTURE), f"偽陽性: {sample}"


def test_allows_own_project_compound():
    """自プロジェクト名は禁止語を部分に含むが正当。"""
    for sample in (
        "social-foundry Epic #463 のスコア推移",
        "~/dev/social-foundry/.worktrees/ 配下",
        "social-foundry-email-hook-release ブランチ",  # 派生した長い複合語
    ):
        assert not _banned_tokens(sample), f"偽陽性: {sample}"


# ---- リポジトリ横断の不変条件 (これが CI ゲート本体) ----
def test_no_vendor_fingerprint_in_tracked_files():
    findings = []
    for path_str in _tracked_files():
        if path_str in _EXEMPT or Path(path_str).suffix.lower() in _SKIP_SUFFIXES:
            continue
        # パス自体も照合する。過去の実混入は docs/<語>-redesign-analysis.md という
        # ファイル名だった。内容だけ見ていると素通りする。
        if _banned_tokens(path_str):
            findings.append(f"  {path_str} (ファイル名)")

        path = REPO_ROOT / path_str
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _banned_tokens(line):
                findings.append(f"  {path_str}:{lineno}")

    assert not findings, (
        f"禁止語 (ベンダー固有名詞) が {len(findings)} 箇所に混入しています。"
        "一般概念語 (ontology / object / property / link / action / lineage 等) に"
        "置き換えてください。該当箇所:\n" + "\n".join(findings)
    )
