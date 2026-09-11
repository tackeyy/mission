"""Issue #768: git project の review-finalize が --base-sha / --head-sha を案内する.

`_revision_scope_from_args` は両フラグが未指定なら `not-applicable` を返し、
`_validate_revision_scope` は project が git repo ならそれを拒否する。つまり
**git project では両フラグが必須**だが、エラー文は何を渡せば通るかを言わなかった。

ここで固定するのは 3 つ。

1. git project で両フラグ未指定なら、エラー文が両フラグ名を含む
2. 「レビュー時に固定した SHA を渡す」ことが読み取れる (実行時に算出すると
   `rev-parse HEAD == head_sha` の検査が空回りするため)
3. 両フラグを渡せば git project でも成立する / non-git project は従来どおり通る
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("gs_revision_scope", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_git_project(root: Path) -> str:
    """1 commit だけの git repo にして HEAD の SHA を返す."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("probe\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def _write_review(path: Path, perspective: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "mission-review/1",
                "iteration": 1,
                "perspective": perspective,
                "scores": {
                    "mission_achievement": 4.5,
                    "accuracy": 4.4,
                    "completeness": 4.3,
                    "usability": 4.2,
                },
                "findings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def review_inputs(tmp_path: Path) -> list[str]:
    return [
        str(_write_review(tmp_path / "review-a.json", "quality")),
        str(_write_review(tmp_path / "review-b.json", "risk")),
    ]


_WINDOW = "2026-09-11T00:00:00Z..2026-09-11T00:05:00Z"


def _finalize(run_cli, state_dir: Path, review_inputs: list[str], *extra: str):
    args = ["review-finalize", "--iteration", "1"]
    for path in review_inputs:
        args += ["--input", path]
    # #350: reviewer 2 名以上では全 perspective の window 報告が必須。
    for perspective in ("quality", "risk"):
        args += ["--reviewer-window", f"{perspective}={_WINDOW}"]
    return run_cli(*args, *extra, cwd=state_dir.parent)


def test_git_project_without_shas_names_both_flags(
    state_dir, run_cli, review_inputs
):
    _make_git_project(state_dir.parent)

    result = _finalize(run_cli, state_dir, review_inputs)

    assert result.returncode == 2
    assert "--base-sha" in result.stderr
    assert "--head-sha" in result.stderr


def test_git_project_guidance_says_to_use_the_reviewed_sha(
    state_dir, run_cli, review_inputs
):
    # 実行時に `git rev-parse HEAD` で算出した値を渡すと、`rev-parse HEAD == head_sha`
    # の検査は常に成立して空回りする。案内は「レビュー時に固定した SHA」を求める。
    _make_git_project(state_dir.parent)

    result = _finalize(run_cli, state_dir, review_inputs)

    assert result.returncode == 2
    assert "レビュー" in result.stderr
    assert "算出" in result.stderr


def test_git_project_with_reviewed_shas_is_accepted(
    state_dir, run_cli, review_inputs, tmp_path
):
    head = _make_git_project(state_dir.parent)

    result = _finalize(
        run_cli,
        state_dir,
        review_inputs,
        "--base-sha",
        head,
        "--head-sha",
        head,
        "--out",
        str(tmp_path / "scoring.json"),
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "scope",
    [
        {"kind": "not-applicable"},
        {"kind": "not-applicable", "reason_code": "other"},
        {"kind": "not-applicable", "reason_code": "non-git", "extra": 1},
    ],
    ids=["missing-reason", "wrong-reason", "extra-key"],
)
def test_malformed_not_applicable_is_invalid_not_a_missing_flag(tmp_path, scope):
    """形が壊れた not-applicable は「引数不足」ではなく「形の不正」として落とす.

    2 つを同じ文言にすると、直し方が違う失敗を同じ案内へ倒すことになる
    (フラグを足しても直らないのに、フラグを足せと言うことになる)。
    `_validate_revision_scope` は保存済み provenance の scope も検査する
    (`_revalidate_score_provenance`) ので、この分岐は本番でも到達しうる。
    """
    module = _load_mission_state()

    with pytest.raises(ValueError) as caught:
        module._validate_revision_scope(tmp_path, scope)

    message = str(caught.value)
    assert "invalid" in message
    assert "--base-sha" not in message
    assert "--head-sha" not in message


def test_well_formed_not_applicable_in_git_project_names_both_flags(tmp_path):
    """形は正しいが git project、のときだけフラグ不足として案内する."""
    _make_git_project(tmp_path)
    module = _load_mission_state()

    with pytest.raises(ValueError) as caught:
        module._validate_revision_scope(
            tmp_path, {"kind": "not-applicable", "reason_code": "non-git"}
        )

    message = str(caught.value)
    assert "--base-sha" in message
    assert "--head-sha" in message


@pytest.mark.parametrize(
    "args",
    [
        ["--base-sha", "0" * 40],
        ["--head-sha", "0" * 40],
        ["--base-sha", "0" * 40, "--head-sha", "abc"],
        ["--base-sha", "not-a-sha", "--head-sha", "0" * 40],
    ],
    ids=["base-only", "head-only", "short-head", "non-hex-base"],
)
def test_partial_or_malformed_shas_are_rejected(state_dir, run_cli, review_inputs, args):
    """片方だけ・40 桁でない SHA は拒否する.

    本 PR が触った分岐ではないが同じ関数にあり、ここが緩むと
    「レビューした head を宣言する」という設計が崩れるので固定する。
    """
    result = _finalize(run_cli, state_dir, review_inputs, *args)

    assert result.returncode == 2
    assert "40" in result.stderr


def test_skill_md_documents_both_flags_and_the_reviewed_sha_rule():
    """SKILL.md の案内が退行しないよう固定する.

    本 issue の原因の半分は「文書どおりに呼ぶと必ず止まる」ことだった。実装だけ直して
    文書が戻ると同じ失敗が再発するので、記述そのものを検査する
    (独立 Checker が、記述を丸ごと削る変異を全テストが通すことを検出した)。
    """
    skill = (
        Path(__file__).resolve().parent.parent / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "--base-sha" in skill
    assert "--head-sha" in skill
    # 「レビュー時に固定した SHA を渡す」ことまで書かれていること。実行時に算出すると
    # rev-parse HEAD == head_sha の検査が空回りするので、この一文が案内の要になる。
    assert "レビュー時に固定した" in skill


def test_non_git_project_still_accepts_omitted_shas(
    state_dir, run_cli, review_inputs, tmp_path
):
    # git repo でないツリーでは従来どおり not-applicable が通る。
    assert not (state_dir.parent / ".git").exists()

    result = _finalize(
        run_cli, state_dir, review_inputs, "--out", str(tmp_path / "scoring.json")
    )

    assert result.returncode == 0, result.stderr
