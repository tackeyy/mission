"""プライベート repository 名の再混入ガード (RED→GREEN).

本リポジトリは PUBLIC。監査ログ・ベンチ成果物・実行ログは、エージェントが読んだ
ものをそのまま含むため、無関係な非公開 repository の名前・Issue 番号・機能説明を
持ち込む。実際に tracked ファイル全体で 105 箇所が公開されていた。

既存の 2 ガードはこの面を塞げない:

- `test_vendor_fingerprint.py` は **ベンダー用語** が対象。自社/私有の repository
  名は禁止語に入っておらず素通りする。
- `test_artifact_hygiene.py` は **home path と個人メモ出力** が対象。名前は見ない。

よって同じ「後から生成される派生物」経路の不変条件として、独立に検査する。

禁止語は **平文で持たない**。リスト自体が「何を伏せているか」の開示になるため、
sha256 の先頭 16 桁で照合する。語を追加するには:

    python3 -c "import hashlib;print(hashlib.sha256(b'<語(小文字)>').hexdigest()[:16])"

を実行し、出力を _BANNED_HASHES に足す。

エントリ数は placeholder の種類数と一致しない。同じプロジェクトが複数の表記
(`-` 区切りと `_` 区切り等) で現れる場合、表記ごとに hash が要るが、置換先の
placeholder は 1 つだからである。
"""
import hashlib
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_BANNED_HASHES = frozenset({
    "411d5c937c1b43a0",
    "9a345efb9475bea5",
    "29ccdd4eba4f523c",
    "8582a9b97a3ef0c2",
    "cb65305ff9ee7ec5",
    "8bc0562fc34753e5",
    "28042787b737d341",
    "f9c5687f36d82a3c",
})

# ASCII 英数字・アンダースコア・ハイフンの連なりを 1 チャンクとして切り出す。
# 日本語との境界を \b / \w に頼らないのが要点: Python の \w は Unicode 単語文字を
# 含むため、禁止語が日本語に直結した形 ("<語> ランで障害") を取りこぼす。
_CHUNK_RE = re.compile(r"[A-Za-z0-9_-]+")

_EXEMPT = {"skills/mission/tests/test_private_project_names.py"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip", ".woff", ".woff2"}


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _banned_tokens(line, banned=_BANNED_HASHES):
    """行から禁止語トークンを拾う。見つからなければ空リスト。"""
    found = []
    for chunk in _CHUNK_RE.findall(line):
        lowered = chunk.lower()
        # 複合語そのものと、区切りで分解した各部分の両方を照合する。実行ログ由来の
        # 再混入は `<語>-worktrees` や `dev_<語>_audit` のような形で入る。
        candidates = [lowered]
        candidates += [p for p in re.split(r"[-_]+", lowered) if p]
        for candidate in candidates:
            if _h(candidate) in banned:
                found.append(candidate)
                break
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
    assert _banned_tokens("zzsynthetic の Issue #12 を修正", _FIXTURE)


def test_detects_token_adjacent_to_japanese():
    """\\b / \\w は Unicode 単語文字を含むため、日本語直結で取りこぼしやすい。"""
    assert _banned_tokens("zzsyntheticランで ConnectionRefused", _FIXTURE)


def test_detects_token_inside_separated_identifier():
    """実行ログ由来のパス・識別子で再混入した実例の形。"""
    assert _banned_tokens("~/dev/zzsynthetic/.worktrees/ 配下", _FIXTURE)
    assert _banned_tokens("project_root=/dev/zzsynthetic 不存在", _FIXTURE)
    assert _banned_tokens("docs/zzsynthetic-audit-2026-06-15.md", _FIXTURE)


def test_is_case_insensitive():
    """監査ログは表記ゆれを保存する (文頭で大文字化される等)。"""
    for sample in ("ZZSynthetic ラン", "Zzsynthetic ラン", "ZZSYNTHETIC ラン"):
        assert _banned_tokens(sample, _FIXTURE), f"取りこぼし: {sample}"


def test_ignores_substrings():
    """部分一致で拾うと一般語が大量に偽陽性になる。"""
    for sample in ("zzsyntheticality", "prezzsynthetic", "zzsynthetics"):
        assert not _banned_tokens(sample, _FIXTURE), f"偽陽性: {sample}"


def test_ignores_unrelated_text():
    assert not _banned_tokens("mission の scoring gate を修正", _FIXTURE)


# ---- リポジトリ横断の不変条件 ----
def test_no_private_project_name_in_tracked_files():
    findings = []
    for path_str in _tracked_files():
        if path_str in _EXEMPT or Path(path_str).suffix.lower() in _SKIP_SUFFIXES:
            continue
        # パス自体も検査する。過去の実混入はファイル名だった。
        hits = set(_banned_tokens(path_str))
        try:
            text = (REPO_ROOT / path_str).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            text = ""
        for line in text.splitlines():
            hits.update(_banned_tokens(line))
        if hits:
            findings.append(f"  {path_str} ({len(hits)} 種)")

    assert not findings, (
        f"プライベート repository 名が {len(findings)} ファイルに残っています。"
        "中立な placeholder (project-a 等) へ置換してください:\n" + "\n".join(findings)
    )
