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

# 推定による分類であることを示す接尾辞。digest ベースの実測と**混ぜない**。
# 信頼度の違う数字を同じ箱に入れると集計値の意味が壊れる。
INFERRED_SUFFIX = "-inferred"

# mission 単位の代表値を決めるときの優先順位。
# 1 度でも改善していればそのミッションでは gate が働いたと扱う。
_OUTCOME_PRIORITY = (
    IMPROVED,
    CHANGED_NO_GAIN,
    NO_CHANGE,
    IMPROVED + INFERRED_SUFFIX,
    CHANGED_NO_GAIN + INFERRED_SUFFIX,
    NO_CHANGE + INFERRED_SUFFIX,
    UNKNOWN,
)


def _composite(entry):
    value = entry.get("composite") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _digest(entry):
    value = entry.get("artifact_digest") if isinstance(entry, dict) else None
    return value if isinstance(value, str) and value else None


def _head_sha(entry):
    """git の head_sha を返す。作業変化の**代理指標**であり digest ではない。

    artifact_digest は #593 以降の run にしか無い。commit を伴う mission なら
    iteration 間の head_sha 変化が「作業が実際に変わったか」の代理になる。
    git 以外の revision_scope は代理指標として使わない。
    """
    if not isinstance(entry, dict):
        return None
    scope = entry.get("revision_scope")
    if not isinstance(scope, dict):
        provenance = entry.get("score_provenance")
        scope = provenance.get("revision_scope") if isinstance(provenance, dict) else None
    if not isinstance(scope, dict) or scope.get("kind") != "git":
        return None
    value = scope.get("head_sha")
    return value if isinstance(value, str) and value else None


def classify_transition(previous, current):
    """iteration N -> N+1 を 1 件分類する。

    digest または composite が欠けている場合は UNKNOWN を返す。
    **推定で埋めない** (過去 state は digest を持たない)。
    """
    prev_digest, curr_digest = _digest(previous), _digest(current)
    prev_score, curr_score = _composite(previous), _composite(current)
    if prev_score is None or curr_score is None:
        return UNKNOWN
    suffix = ""
    if prev_digest is None or curr_digest is None:
        # digest が無い過去 state は head_sha で遡及推定する。
        # 実測より弱い根拠なので、必ず -inferred を付けて区別する。
        prev_digest, curr_digest = _head_sha(previous), _head_sha(current)
        if prev_digest is None or curr_digest is None:
            return UNKNOWN
        suffix = INFERRED_SUFFIX
    if prev_digest == curr_digest:
        # 成果物が変わっていない。スコアだけ動いていても採点のばらつきで
        # あって修正の成果ではないため、改善として数えない。
        return NO_CHANGE + suffix
    return (IMPROVED if curr_score > prev_score else CHANGED_NO_GAIN) + suffix


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

    digest による実測と head_sha による遡及推定は **別の箱に集計する**
    (`inferred` キー)。信頼度の違う数字を混ぜると集計値の意味が壊れる。
    """
    def _blank_counts():
        return {IMPROVED: 0, CHANGED_NO_GAIN: 0, NO_CHANGE: 0, UNKNOWN: 0}

    counts = _blank_counts()
    inferred_counts = _blank_counts()
    false_positive_candidates = []
    inferred_false_positive_candidates = []
    missions_total = 0
    missions_multi_iteration = 0
    inferred_multi_iteration = 0

    for state in states:
        missions_total += 1
        result = classify_state(state)
        if not result["transitions"]:
            continue
        outcome = result["mission_outcome"]
        mission_name = state.get("mission") if isinstance(state, dict) else None
        if isinstance(outcome, str) and outcome.endswith(INFERRED_SUFFIX):
            base = outcome[: -len(INFERRED_SUFFIX)]
            inferred_multi_iteration += 1
            if base in inferred_counts:
                inferred_counts[base] += 1
            if base == NO_CHANGE:
                inferred_false_positive_candidates.append({
                    "mission": mission_name, "transitions": result["transitions"],
                })
            continue
        missions_multi_iteration += 1
        if outcome in counts:
            counts[outcome] += 1
        if outcome == NO_CHANGE:
            false_positive_candidates.append({
                "mission": mission_name, "transitions": result["transitions"],
            })

    def _rate(bucket_counts, multi):
        # FP 率の母数は「判定できたミッション」に限る。unknown だけの母集団で
        # 0.0 を返すと「FP なし」に読めるが実際は「判定できない」であり、
        # 意味のない数字を意味ありげに出すことになる。
        classifiable = multi - bucket_counts[UNKNOWN]
        return classifiable, (bucket_counts[NO_CHANGE] / classifiable if classifiable else None)

    classifiable, rate = _rate(counts, missions_multi_iteration)
    inferred_classifiable, inferred_rate = _rate(inferred_counts, inferred_multi_iteration)

    return {
        "missions_total": missions_total,
        "missions_multi_iteration": missions_multi_iteration,
        "missions_classifiable": classifiable,
        "counts": counts,
        "false_positive_candidates": false_positive_candidates,
        "false_positive_rate": rate,
        # 遡及推定 (head_sha ベース)。実測より弱い根拠なので分離して報告する。
        "inferred": {
            "basis": "revision_scope.head_sha change between iterations",
            "missions_multi_iteration": inferred_multi_iteration,
            "missions_classifiable": inferred_classifiable,
            "counts": inferred_counts,
            "false_positive_candidates": inferred_false_positive_candidates,
            "false_positive_rate": inferred_rate,
        },
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
            if (
                isinstance(entry, dict)
                and entry.get("kind", "execution") == "execution"
                and entry.get("status") in ("passed", "failed")
            ):
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
