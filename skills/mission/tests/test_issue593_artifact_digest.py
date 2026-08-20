"""#593 B-1: iteration ごとの artifact digest を score_history に記録する。

背景: mission は iteration ごとの artifact スナップショットを保存しない
(`archive-worktree` は全 iteration 完了後に一度だけ走り、artifact は各
iteration で上書きされる)。そのため本番で観測されている
「反復したが composite 不変」15 件について、

  - no-change       : 弾いたのに直すものが無かった = ゲートの誤検知 (FP)
  - changed-no-gain : 直したが改善しなかった = 修正の失敗

を区別できない。対処がまったく異なるのに混ざっている。

本文の保存はしない (サイズ・機密の観点)。sha256 digest のみを記録し、
`artifact_digest[iter N] != artifact_digest[iter N+1]` で変化を判定する。

**gate の意味論は変更しない。** 記録の追加のみ。
"""

import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate

ITEMS = {
    "mission_achievement": 4.0,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 3.5,
}


def _scoring_json(tmp_path, *, iteration=1, name="scoring.json", reviewers=1):
    evidence_path, ref, claim = write_canonical_review_aggregate(
        tmp_path, [canonical_review(ITEMS, perspective=f"fixture-{i}") for i in range(reviewers)], iteration=iteration,
    )
    payload = {
        "items": ITEMS,
        "open_high": 0,
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _set_artifact(state_dir, rel_path):
    session = state_dir / "sessions" / "test.json"
    data = json.loads(session.read_text())
    data["artifact"] = {"path": rel_path, "required_for_pass": False}
    session.write_text(json.dumps(data, indent=2))


def _push(run_cli, state_dir, tmp_path, iteration, reviewers=1):
    src = _scoring_json(tmp_path, iteration=iteration,
                        name=f"scoring-{iteration}.json", reviewers=reviewers)
    return run_cli("push-score", "--iteration", str(iteration), "--scoring-json", str(src),
                   cwd=state_dir.parent)


def test_artifact_digest_recorded_for_iteration(state_dir, run_cli, read_state, tmp_path):
    root = state_dir.parent
    (root / "artifact.md").write_text("# v1\nfindings: a\n", encoding="utf-8")
    _set_artifact(state_dir, "artifact.md")

    assert _push(run_cli, state_dir, tmp_path, 1).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    assert entry["artifact_digest"].startswith("sha256:")
    assert entry["artifact_digest_status"] == "ok"


def test_digest_changes_only_when_artifact_changes(state_dir, run_cli, read_state, tmp_path):
    """反復して成果物が実際に変わったかを判定できる (本 Issue の核心)。"""
    root = state_dir.parent
    artifact = root / "artifact.md"
    artifact.write_text("# v1\n", encoding="utf-8")
    _set_artifact(state_dir, "artifact.md")

    assert _push(run_cli, state_dir, tmp_path, 1).returncode == 0
    artifact.write_text("# v2 revised\n", encoding="utf-8")
    assert _push(run_cli, state_dir, tmp_path, 2).returncode == 0
    assert _push(run_cli, state_dir, tmp_path, 3).returncode == 0

    history = read_state(state_dir)["score_history"]
    d1, d2, d3 = (e["artifact_digest"] for e in history[:3])
    assert d1 != d2, "artifact が変わったのに digest が同じ"
    assert d2 == d3, "artifact が変わっていないのに digest が違う"


def test_missing_artifact_records_reason_not_silent_null(state_dir, run_cli, read_state, tmp_path):
    """収集できない事実を握りつぶさない。

    digest が null で理由も null だと「artifact 未設定」と「読めなかった」を
    区別できない。
    """
    _set_artifact(state_dir, "does-not-exist.md")
    assert _push(run_cli, state_dir, tmp_path, 1).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    assert entry["artifact_digest"] is None
    assert entry["artifact_digest_status"] == "missing"


def test_unconfigured_artifact_is_distinguishable_from_missing(state_dir, run_cli, read_state, tmp_path):
    assert _push(run_cli, state_dir, tmp_path, 1).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    assert entry["artifact_digest"] is None
    assert entry["artifact_digest_status"] == "not-configured"


def test_digest_failure_never_blocks_push_score(state_dir, run_cli, read_state, tmp_path):
    """digest 取得の失敗で mission を止めない (fail-open)。"""
    root = state_dir.parent
    (root / "artifact_dir").mkdir()
    _set_artifact(state_dir, "artifact_dir")  # ディレクトリを指す異常系
    result = _push(run_cli, state_dir, tmp_path, 1)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    entry = read_state(state_dir)["score_history"][0]
    assert entry["artifact_digest"] is None
    assert entry["artifact_digest_status"] not in (None, "ok")


def test_gate_semantics_unchanged_by_digest_recording(state_dir, run_cli, read_state, tmp_path):
    """digest の記録が pass gate の判定に影響しないこと。"""
    root = state_dir.parent
    (root / "artifact.md").write_text("# v1\n", encoding="utf-8")
    _set_artifact(state_dir, "artifact.md")
    # reviewer 2 名にして agreement 算出経路まで通す (1 名だと agreement は
    # 元から None であり、gate 経路が生きているかを検証できない)。
    assert _push(run_cli, state_dir, tmp_path, 1, reviewers=2).returncode == 0

    entry = read_state(state_dir)["score_history"][0]
    # gate が参照するフィールドが従来どおり揃っている
    assert entry["composite"] == 4.0
    assert entry["min_item"] == 3.5
    assert entry["open_high"] == 0
    assert entry["findings_evidence_path"]
    assert entry["review_agreement"] is not None
