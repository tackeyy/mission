"""Issue #126: review agreement is independent from composite and gates pass."""

from __future__ import annotations

import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _write_evidence(state_dir, *, delta):
    reviewer_a = dict(ITEMS, mission_achievement=5.0)
    reviewer_b = dict(ITEMS, mission_achievement=5.0 - delta)
    return write_canonical_review_aggregate(
        state_dir.parent,
        [
            canonical_review(reviewer_a, perspective="A"),
            canonical_review(reviewer_b, perspective="B"),
        ],
        name_prefix="review-agreement",
    )


def _write_scoring_to(path, evidence):
    evidence_path, ref, claim = evidence
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"],
        "findings_evidence_path": str(evidence_path),
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
    }
    payload["score_provenance"] = {"score_source": "scoring-json", "review_evidence_ref": ref,
                                   "revision_scope": ref["revision_scope"]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_scoring(tmp_path, evidence):
    return _write_scoring_to(tmp_path / "scoring.json", evidence)


def test_aggregate_reviews_outputs_derived_consensus_and_independent_agreement(state_dir, run_cli, tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    base = {
        "schema": "mission-review/1",
        "iteration": 1,
        "scores": ITEMS,
        "findings": [],
        "same_score_note": None,
    }
    a.write_text(json.dumps(dict(base, perspective="A")), encoding="utf-8")
    b.write_text(json.dumps(dict(base, perspective="B", scores=dict(ITEMS, mission_achievement=3.5))), encoding="utf-8")
    out = tmp_path / "out.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out),
            "--reviewer-window", "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
            "--reviewer-window", "B=2026-08-02T10:00:30Z..2026-08-02T10:04:00Z",
            cwd=state_dir.parent, check=True)

    payload = json.loads(out.read_text())
    assert set(payload["items"]) == {"mission_achievement", "accuracy", "completeness", "usability"}
    assert payload["review_agreement"] == 4.0
    assert payload["review_agreement"] == 4.0
    assert payload["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_push_score_records_review_agreement_independently(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.0)
    scoring = _write_scoring(tmp_path, evidence)

    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    entry = read_state(state_dir)["score_history"][-1]
    assert entry["items"] == ITEMS
    assert entry["composite"] == 4.35
    assert entry["review_agreement"] == 4.0
    assert entry["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_mark_passes_rejects_max_delta_above_1_5(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.6)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "低合意" in r.stderr
    assert "mission_achievement" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_low_agreement_guidance_points_to_critic_not_more_reviews(
    state_dir, run_cli, read_state, tmp_path
):
    """#869: 案内どおり追加レビューを足しても max-min は縮まらないため、reject の
    案内は他 threshold gate (composite/min item) と同じく Critic 起動 → 次イテレー
    ションを促す。従っても同じエラーが返り続ける旧文言 (「追加レビュー…再集計して
    ください」) は返さない。"""
    reviewer_a = dict(ITEMS, completeness=5.0)
    reviewer_b = dict(ITEMS, completeness=4.0)
    reviewer_c = dict(ITEMS, completeness=3.0)
    evidence = write_canonical_review_aggregate(
        state_dir.parent,
        [
            canonical_review(reviewer_a, perspective="A"),
            canonical_review(reviewer_b, perspective="B"),
            canonical_review(reviewer_c, perspective="C"),
        ],
        name_prefix="review-agreement-3reviewer",
    )
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "低合意" in r.stderr
    assert "completeness" in r.stderr
    assert read_state(state_dir)["passes"] is False
    assert "Critic を起動し次イテレーションへ進んでください" in r.stderr
    # 従っても max-min が縮まらない旧案内が復活していないことを固定する
    assert "追加レビュー 1 名を実施して再集計してください" not in r.stderr


def test_low_agreement_reject_progresses_to_pass_via_the_guided_command_sequence(
    state_dir, run_cli, read_state, tmp_path
):
    """#869 受け入れ条件: 低合意 reject の案内 (Critic 起動→次イテレーション) が実際に
    指す先を辿ると mission が進む (pass できる) ことを固定する。

    案内が指す手順は次の実装から特定した:
    - `next` は reject 後も `score_history` に現在 iteration の有効な採点がある限り
      無条件で `mark-passes` を返し続ける (状態非依存。next_action.py:343-387)。
      mission-critic (Skill) を起動して次イテレーションへ進むこと自体は `next` では
      検出・強制されない。
    - `mark-passes` reject は kernel transaction 全体を rollback するため
      (mission_application/review.py:441 の `with repository.transaction():` 内で
      raise)、state に reject 自体の記録は残らない。passes は reject 前と同じ
      False のまま。
    - iteration を進めるには `push-score --iteration <N+1>` を呼ぶ以外の手段はなく
      (bin/mission-state.py:13774 `data["iteration"] = args.iteration` が
      top-level iteration の唯一の書き込み元)、それには reviewing フェーズへ戻る
      必要がある。
    - `phase` は `scoring` のまま `advance --phase planning` を呼ぶと、kernel の
      Advance 契約 (mission_kernel/transitions.py:715-722: target は EXECUTING/
      REVIEWING のみ許可、SCORING からの遷移は kernel には無い) には合致しないが、
      application 層の legacy-compat フォールバック
      (mission_application/lifecycle.py:952-972 の `else: repository.save(proposed)`)
      が kernel の reject を素通りさせて phase を書き込む。続く
      `advance --phase executing` / `advance --phase reviewing` は kernel が許可する
      正規の遷移 (PLANNING→EXECUTING→REVIEWING)。
    """
    # iteration 1: 3 reviewer で completeness 5/4/3 (max-min=2.0) -> reject
    reviewer_a = dict(ITEMS, completeness=5.0)
    reviewer_b = dict(ITEMS, completeness=4.0)
    reviewer_c = dict(ITEMS, completeness=3.0)
    evidence1 = write_canonical_review_aggregate(
        state_dir.parent,
        [
            canonical_review(reviewer_a, perspective="A"),
            canonical_review(reviewer_b, perspective="B"),
            canonical_review(reviewer_c, perspective="C"),
        ],
        name_prefix="reach-pass-iter1",
    )
    scoring1 = _write_scoring(tmp_path, evidence1)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring1), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "Critic を起動し次イテレーションへ進んでください" in r.stderr
    assert read_state(state_dir)["passes"] is False

    # next は reject 後も iteration 1 のまま mark-passes を繰り返す
    # (Issue が報告した「進める経路が無い」ように見える状態そのもの)
    stuck = json.loads(run_cli("next", cwd=state_dir.parent, check=True).stdout)
    assert stuck["next_action"] == "mark-passes"
    assert stuck["iteration"] == 1

    # 案内が指す実際の手順: critic (Skill) の後、advance で
    # scoring -> planning -> executing -> reviewing と進める
    r = run_cli("advance", "--phase", "planning", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "advance", "--phase", "executing", "--activity", "active:implementation",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "advance", "--phase", "reviewing", "--activity", "reviewer-wait:review-response",
        "--artifact-applicability", "not-applicable",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr

    # iteration 2: 合意のある採点 (max-min <= 1.0) を入れる
    reviewer_a2 = dict(ITEMS, completeness=4.2)
    reviewer_b2 = dict(ITEMS, completeness=4.0)
    evidence2 = write_canonical_review_aggregate(
        state_dir.parent,
        [
            canonical_review(reviewer_a2, perspective="A"),
            canonical_review(reviewer_b2, perspective="B"),
        ],
        iteration=2,
        name_prefix="reach-pass-iter2",
    )
    scoring2 = _write_scoring_to(tmp_path / "scoring2.json", evidence2)
    run_cli("push-score", "--iteration", "2", "--scoring-json", str(scoring2), cwd=state_dir.parent, check=True)

    progressed = json.loads(run_cli("next", cwd=state_dir.parent, check=True).stdout)
    assert progressed["iteration"] == 2
    assert progressed["next_action"] == "mark-passes"

    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_warns_for_delta_above_1_0_and_passes(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.1)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "reviewer agreement is low" in r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_allows_delta_at_1_5_boundary(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.5)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True
