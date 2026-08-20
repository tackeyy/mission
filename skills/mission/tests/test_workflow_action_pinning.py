"""third-party action の SHA pin ガード (RED→GREEN).

`uses: owner/repo@v7` は **可変 tag** を指す。tag は上流が動かせるため、pin した
つもりで別のコードを実行しうる。public repo の workflow は fork PR の diff を
checkout して走るので、ここが緩いと供給網の入口になる。

GitHub の repo 設定にも `sha_pinning_required` があるが、これは有料機能に依存し、
また設定は repo 外から見えない。workflow ファイル自体を不変条件として検査する。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# `uses: <owner>/<repo>[/<path>]@<ref>` の <ref> を取り出す。
# docker:// 形式とローカル相対参照 (./...) は対象外。
_USES_RE = re.compile(r"^\s*uses:\s*(?P<action>[^\s@]+)@(?P<ref>\S+)", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _workflow_files():
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def _external_uses():
    """(workflow 名, action, ref) を返す。ローカル参照と docker は除く。"""
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        for m in _USES_RE.finditer(text):
            action, ref = m.group("action"), m.group("ref")
            if action.startswith("./") or action.startswith("docker:"):
                continue
            yield path.name, action, ref


# ---- 判定ロジックの単体テスト ----
def test_full_sha_is_recognised():
    assert _FULL_SHA_RE.match("3d3c42e5aac5ba805825da76410c181273ba90b1")


def test_tags_and_short_shas_are_rejected():
    for ref in ("v7", "v7.1.0", "main", "3d3c42e", "latest"):
        assert not _FULL_SHA_RE.match(ref), f"pin とみなしてはいけない: {ref}"


# ---- リポジトリ横断の不変条件 ----
def test_every_external_action_is_pinned_to_a_full_sha():
    unpinned = [
        f"  {wf}: {action}@{ref}"
        for wf, action, ref in _external_uses()
        if not _FULL_SHA_RE.match(ref)
    ]
    assert not unpinned, (
        "third-party action が可変 ref を参照しています。40 桁の commit SHA へ "
        "pin してください (追従は Dependabot が PR で出します):\n" + "\n".join(unpinned)
    )


def test_pinned_actions_keep_a_version_comment():
    """SHA だけでは人間が版を追えない。`# vN` を必須にして可読性を保つ。"""
    missing = []
    for path in _workflow_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _USES_RE.match(line)
            if not m or m.group("action").startswith(("./", "docker:")):
                continue
            if _FULL_SHA_RE.match(m.group("ref")) and "#" not in line:
                missing.append(f"  {path.name}: {line.strip()}")
    assert not missing, (
        "SHA pin に版コメント (`# v7` 等) がありません:\n" + "\n".join(missing)
    )


def test_at_least_one_external_action_is_checked():
    """workflow の書式が変わって 0 件になると、本ガードが黙って無効化される。"""
    assert list(_external_uses()), "external action を 1 つも検出できていない"
