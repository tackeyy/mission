"""公開成果物の衛生ガード (RED→GREEN).

`#118` は配布 ref ファイルに限ってメンテナの home path 混入を禁止した。しかし
benchmark の実行ログや監査レポートは別経路で生成されるため同じ規則が及ばず、
実際に tracked ファイル全体で 2,290 箇所のローカルパスと、ローカルの個人メモ
ファイルを丸ごと取り込んだ数万文字の出力が公開されていた。

再発経路は固有名詞ガード (test_vendor_fingerprint.py) と同じで、「後から生成
される派生物」。よって同じくリポジトリ横断の不変条件として検査する。
"""
import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# 匿名化済みのプレースホルダは許可する。実在ユーザー名だけを弾く。
_ALLOWED_HOME_SEGMENTS = {"<user>", "x", "USER", "$USER", "runner"}
_HOME_PATH_RE = re.compile(r"/Users/([^/\s`\"')\\]+)/")

# 検出パターン自体を定義するテストは対象外。
_EXEMPT = {
    "skills/mission/tests/test_artifact_hygiene.py",
    "skills/mission/tests/test_doc_consistency.py",  # #118 の _HOME_PATH_RE を定義する
}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip", ".woff", ".woff2"}

# ローカルの個人メモを丸ごと取り込んだ出力の目印。実行ログが `rg`/`cat` で
# 読み出した結果がそのまま artifacts に固定されてしまう。
_PERSONAL_STORE_HINTS = ("/memories/MEMORY.md", "/.claude/projects/", "/.codex/memories/")
_REDACTION_MARKER = "[redacted: personal memory store output]"


def _tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _scannable_files():
    for path_str in _tracked_files():
        if path_str in _EXEMPT or Path(path_str).suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            text = (REPO_ROOT / path_str).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        yield path_str, text


def _home_path_hits(text):
    return [m.group(1) for m in _HOME_PATH_RE.finditer(text)
            if m.group(1) not in _ALLOWED_HOME_SEGMENTS]


# ---- 判定ロジックの単体テスト ----
def test_flags_real_username_in_home_path():
    assert _home_path_hits("cwd=/Users/someone/dev/mission") == ["someone"]


def test_allows_anonymised_placeholders():
    for sample in ("/Users/<user>/dev/mission", "/Users/x/tmp", "/Users/runner/work"):
        assert not _home_path_hits(sample), f"偽陽性: {sample}"


def test_ignores_non_home_paths():
    for sample in ("/usr/local/bin", "~/dev/mission", "C:/Users"):
        assert not _home_path_hits(sample), f"偽陽性: {sample}"


# ---- リポジトリ横断の不変条件 ----
def test_no_maintainer_home_path_in_tracked_files():
    findings = []
    for path_str, text in _scannable_files():
        hits = set(_home_path_hits(text))
        if hits:
            findings.append(f"  {path_str} ({len(hits)} 種)")

    assert not findings, (
        f"実在ユーザー名を含む home path が {len(findings)} ファイルに残っています。"
        "`/Users/<user>/` 等の匿名形へ置換してください:\n" + "\n".join(findings)
    )


def test_no_personal_memory_dump_in_artifacts():
    """実行ログが個人メモの中身を丸ごと固定していないか。"""
    findings = []
    for path_str, text in _scannable_files():
        if not path_str.endswith(".jsonl"):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {})
            output = str(item.get("aggregated_output", ""))
            if _REDACTION_MARKER in output or not output:
                continue
            # 個人メモを読んだのはコマンド側に現れる。出力側にはその中身が載る。
            # 量の多寡で線を引かない: sed/nl による部分読み出しでも中身は中身。
            command = str(item.get("command", ""))
            if any(hint in command for hint in _PERSONAL_STORE_HINTS):
                findings.append(f"  {path_str}:{lineno} ({len(output)} 文字)")

    assert not findings, (
        f"個人メモストアの出力が {len(findings)} 箇所の実行ログに固定されています。"
        f"当該 aggregated_output を `{_REDACTION_MARKER}` に置換してください:\n"
        + "\n".join(findings)
    )
