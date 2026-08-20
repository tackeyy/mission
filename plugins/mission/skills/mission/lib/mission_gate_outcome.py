"""#593 B-2: gate outcome の分類。

pass gate が発火して反復に入ったとき、その反復が何だったのかを機械的に
分類する。本番 451 mission のうち「反復したが composite 不変」15 件は、
現在

  - ゲートの誤検知 (弾いたが直すものが無い)
  - 修正の失敗 (直したが改善しない)

が混ざっている。対処がまったく異なるのに区別できていない。B-1 で記録した
`artifact_digest` を使って両者を分離する。

このモジュールは **判定のみ** を行い、gate の意味論には一切関与しない。
"""
from __future__ import annotations

IMPROVED = "improved"
CHANGED_NO_GAIN = "changed-no-gain"
NO_CHANGE = "no-change"
UNKNOWN = "unknown"

# mission 単位の代表値を決めるときの優先順位。
# 1 度でも改善していればそのミッションでは gate が働いたと扱う。
_OUTCOME_PRIORITY = (IMPROVED, CHANGED_NO_GAIN, NO_CHANGE, UNKNOWN)


def _composite(entry):
    value = entry.get("composite") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _digest(entry):
    value = entry.get("artifact_digest") if isinstance(entry, dict) else None
    return value if isinstance(value, str) and value else None


def classify_transition(previous, current):
    """iteration N -> N+1 を 1 件分類する。

    digest または composite が欠けている場合は UNKNOWN を返す。
    **推定で埋めない** (過去 state は digest を持たない)。
    """
    prev_digest, curr_digest = _digest(previous), _digest(current)
    prev_score, curr_score = _composite(previous), _composite(current)
    if prev_digest is None or curr_digest is None:
        return UNKNOWN
    if prev_score is None or curr_score is None:
        return UNKNOWN
    if prev_digest == curr_digest:
        # 成果物が変わっていない。スコアだけ動いていても採点のばらつきで
        # あって修正の成果ではないため、改善として数えない。
        return NO_CHANGE
    return IMPROVED if curr_score > prev_score else CHANGED_NO_GAIN


def _history(state):
    history = state.get("score_history") if isinstance(state, dict) else None
    if not isinstance(history, list):
        return []
    return [entry for entry in history if isinstance(entry, dict)]


def classify_state(state):
    """1 mission の score_history を分類する。"""
    history = _history(state)
    transitions = []
    for index in range(1, len(history)):
        previous, current = history[index - 1], history[index]
        transitions.append({
            "from_iteration": previous.get("iteration"),
            "to_iteration": current.get("iteration"),
            "outcome": classify_transition(previous, current),
            "composite_from": _composite(previous),
            "composite_to": _composite(current),
            "artifact_changed": (
                None
                if _digest(previous) is None or _digest(current) is None
                else _digest(previous) != _digest(current)
            ),
        })
    outcomes = {transition["outcome"] for transition in transitions}
    mission_outcome = next((o for o in _OUTCOME_PRIORITY if o in outcomes), None)
    return {"transitions": transitions, "mission_outcome": mission_outcome}


def summarize_states(states):
    """#593 B-3: 複数 state を集計する。

    FP 率の母数は **ゲートが発火した (=反復した) ミッション** であり、
    全ミッションではない。素通りしたミッションを母数に入れると率が薄まり、
    ゲート精度の指標にならない。
    """
    counts = {IMPROVED: 0, CHANGED_NO_GAIN: 0, NO_CHANGE: 0, UNKNOWN: 0}
    false_positive_candidates = []
    missions_total = 0
    missions_multi_iteration = 0

    for state in states:
        missions_total += 1
        result = classify_state(state)
        if not result["transitions"]:
            continue
        missions_multi_iteration += 1
        outcome = result["mission_outcome"]
        if outcome in counts:
            counts[outcome] += 1
        if outcome == NO_CHANGE:
            false_positive_candidates.append({
                "mission": (state.get("mission") if isinstance(state, dict) else None),
                "transitions": result["transitions"],
            })

    # FP 率の母数は「判定できたミッション」に限る。unknown だけの母集団で
    # 0.0 を返すと「FP なし」に読めるが実際は「判定できない」であり、
    # 意味のない数字を意味ありげに出すことになる。
    missions_classifiable = missions_multi_iteration - counts[UNKNOWN]
    rate = (
        counts[NO_CHANGE] / missions_classifiable
        if missions_classifiable
        else None
    )
    return {
        "missions_total": missions_total,
        "missions_multi_iteration": missions_multi_iteration,
        "missions_classifiable": missions_classifiable,
        "counts": counts,
        "false_positive_candidates": false_positive_candidates,
        "false_positive_rate": rate,
    }


def false_negative_summary(states):
    """#593 B-4 / #594 A: false negative を verification 結果で客観的に数える。

    FN = 「gate は通したのに verification は失敗した」= 見逃し。
    verification (#594 A) が入るまでは ground truth が無く測れないため、
    その場合は `unmeasurable` を返す。**推定で埋めない。**

    `not-run` を「欠陥なし」と混同しない。検証していないことは
    合格の証拠にならない。
    """
    measurable = []
    false_negatives = []
    considered = 0
    for state in states:
        considered += 1
        if not isinstance(state, dict):
            continue
        history = state.get("verification_history")
        if not isinstance(history, list):
            continue
        latest = None
        for entry in history:
            if isinstance(entry, dict) and entry.get("status") in ("passed", "failed"):
                latest = entry
        if latest is None:
            # not-run しか無い / 空 は「測れない」であって「欠陥なし」ではない
            continue
        measurable.append(state)
        if state.get("passes") is True and latest.get("status") == "failed":
            false_negatives.append(state.get("mission"))

    if not measurable:
        return {
            "status": "unmeasurable",
            "count": None,
            "reason": (
                "false negatives need an objective label; record verification "
                "results (#594 A) so 'review passed but verification failed' "
                "can be counted"
            ),
            "missions_considered": considered,
            "missions_with_verification": 0,
            "false_negative_rate": None,
        }
    return {
        "status": "measured",
        "count": len(false_negatives),
        "missions": false_negatives,
        "missions_considered": considered,
        "missions_with_verification": len(measurable),
        "false_negative_rate": len(false_negatives) / len(measurable),
        "reason": None,
    }
