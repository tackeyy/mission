"""#587: 構造化 findings 照合による採点。

正規表現の共起では推論の有無を測れないと 3 つの独立レビューが結論した。
実測された限界:

  - 語を並べただけの文字列が満点 1.00 を取る
  - 正しい言い換えが 0 点になる
  - 日本語で書かれた正解が 0 点になる
  - 「棄却した」と「主張した」を区別できない (#559 が達成できなかった)

本方式は散文への正規表現マッチを**完全に廃止**し、成果物が出力する
機械可読な findings ブロックと正解キーを**厳密照合**する。

本テストは #598 で事前登録した受け入れ条件をそのまま検証する。
"""

import pytest

from mission_structured_findings import (
    FindingsFormatError,
    parse_findings_block,
    score_findings,
)

# 正解キー: location / key / verdict の 3 つ組で同定する。
ANSWER_KEY = {
    "defects": [
        {"location": "impl-alpha.md", "key": "request_timeout_ms", "expected": "3000", "actual": "27000"},
        {"location": "impl-beta.md", "key": "max_retries", "expected": "3", "actual": "6"},
    ],
    "decoys": [
        {"location": "runbook.md", "key": "idle_timeout_ticks"},
    ],
}


def _block(rows):
    header = "| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
    body = "".join(
        "| {location} | {key} | {expected} | {actual} | {verdict} |\n".format(**r) for r in rows
    )
    return "prose before\n\n" + header + body + "\nprose after\n"


def _row(location, key, expected, actual, verdict):
    return dict(location=location, key=key, expected=expected, actual=actual, verdict=verdict)


# --- パース ------------------------------------------------------------------

def test_parses_findings_block_from_surrounding_prose():
    rows = parse_findings_block(_block([_row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift")]))
    assert rows == [{"location": "impl-alpha.md", "key": "request_timeout_ms",
                     "expected": "3000", "actual": "27000", "verdict": "drift"}]


def test_missing_findings_block_raises_rather_than_scoring_zero():
    """ブロックが無いことを「発見ゼロ」と混同しない。採点不能として扱う。"""
    with pytest.raises(FindingsFormatError):
        parse_findings_block("no table here at all")


def test_unknown_verdict_value_is_rejected():
    bad = _block([_row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "maybe-ish")])
    with pytest.raises(FindingsFormatError):
        parse_findings_block(bad)


# --- 受け入れ条件: 羅列耐性 ---------------------------------------------------

def test_keyword_salad_scores_zero():
    """語と値を並べただけでは 1 行も成立しない。

    現行 marker 方式ではこの文字列が満点 1.00 を取っていた。
    """
    salad = "3000 27000 max_retries 3 6 drift mismatch idle_timeout_ticks violation"
    with pytest.raises(FindingsFormatError):
        parse_findings_block(salad)


def test_shotgun_reporting_is_penalised_by_precision():
    """全部 drift と書けば recall は満点になるが precision が落ちる。"""
    rows = [
        _row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift"),
        _row("impl-beta.md", "max_retries", "3", "6", "drift"),
        _row("runbook.md", "idle_timeout_ticks", "120", "120", "drift"),  # decoy を誤主張
    ]
    result = score_findings(_block(rows), ANSWER_KEY)
    assert result["recall"] == 1.0
    assert result["precision"] < 1.0
    assert result["f1"] < 1.0
    assert result["false_positives"] == ["runbook.md:idle_timeout_ticks"]


# --- 受け入れ条件: 表現非依存・言語非依存 --------------------------------------

def test_score_is_independent_of_prose_wording_and_language():
    """同じ findings なら、周囲の散文が英語でも日本語でも同点になる。

    現行方式では日本語で書かれた正解が 0 点になっていた。
    """
    rows = [_row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift"),
            _row("impl-beta.md", "max_retries", "3", "6", "drift")]
    en = score_findings("Findings below.\n\n" + _block(rows), ANSWER_KEY)
    ja = score_findings("以下に発見事項を示す。\n\n" + _block(rows), ANSWER_KEY)
    assert en["f1"] == ja["f1"] == 1.0


def test_row_order_does_not_change_the_score():
    a = [_row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift"),
         _row("impl-beta.md", "max_retries", "3", "6", "drift")]
    assert score_findings(_block(a), ANSWER_KEY)["f1"] == score_findings(_block(a[::-1]), ANSWER_KEY)["f1"]


# --- 受け入れ条件: 極性の判定 (#559 が達成できなかった要件) ---------------------

def test_correctly_rejecting_a_decoy_is_not_penalised():
    """decoy を no-finding と明示するのは正しい挙動であり減点しない。"""
    rows = [
        _row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift"),
        _row("impl-beta.md", "max_retries", "3", "6", "drift"),
        _row("runbook.md", "idle_timeout_ticks", "120", "120", "no-finding"),
    ]
    result = score_findings(_block(rows), ANSWER_KEY)
    assert result["false_positives"] == []
    assert result["f1"] == 1.0
    assert result["decoys_correctly_rejected"] == 1


def test_claiming_a_real_defect_as_no_finding_costs_recall():
    rows = [
        _row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "no-finding"),
        _row("impl-beta.md", "max_retries", "3", "6", "drift"),
    ]
    result = score_findings(_block(rows), ANSWER_KEY)
    assert result["recall"] == 0.5


# --- 退化入力 -----------------------------------------------------------------

def test_empty_answer_key_does_not_divide_by_zero():
    rows = [_row("x.md", "k", "1", "2", "drift")]
    result = score_findings(_block(rows), {"defects": [], "decoys": []})
    assert result["recall"] is None
    assert result["f1"] is None


def test_no_rows_reported_is_zero_recall_not_an_error():
    """表はあるが 1 行も報告しないのは「何も見つけられなかった」であり、
    フォーマット不備とは区別する。"""
    result = score_findings(_block([]), ANSWER_KEY)
    assert result["recall"] == 0.0
    assert result["false_positives"] == []


def test_duplicate_rows_do_not_inflate_recall():
    rows = [_row("impl-alpha.md", "request_timeout_ms", "3000", "27000", "drift")] * 3
    assert score_findings(_block(rows), ANSWER_KEY)["recall"] == 0.5


def test_wrong_actual_value_does_not_count_as_found():
    """値が違えば同定できていない。location/key が合っていても数えない。"""
    rows = [_row("impl-alpha.md", "request_timeout_ms", "3000", "9999", "drift")]
    result = score_findings(_block(rows), ANSWER_KEY)
    assert result["recall"] == 0.0


# --- 実 run で検出した欠陥の回帰 (#587 パイロット) -----------------------------

def test_thousands_separators_do_not_break_matching():
    """`4,127` と `4127` を別物と扱わない。

    実 run で、実質的に完全正解の artifact が f1=0.0 になった。桁区切りの
    有無だけで不一致になっていた。正しい答えを書式の理由で 0 点にしない。
    """
    key = {"defects": [{"location": "s.md", "key": "total_signups",
                        "expected": "4217", "actual": "4127"}], "decoys": []}
    table = ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
             "| s.md | total_signups | 4,217 | 4,127 | drift |\n")
    assert score_findings(table, key)["recall"] == 1.0


def test_value_matching_ignores_case_and_surrounding_whitespace():
    key = {"defects": [{"location": "s.md", "key": "backoff",
                        "expected": "exponential", "actual": "constant-interval"}], "decoys": []}
    table = ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
             "| s.md | backoff | Exponential |  Constant-Interval  | drift |\n")
    assert score_findings(table, key)["recall"] == 1.0


def test_key_matching_is_insensitive_to_punctuation_and_case():
    """`Total Signups` と `total_signups` を別物と扱わない。"""
    key = {"defects": [{"location": "s.md", "key": "total_signups",
                        "expected": "4217", "actual": "4127"}], "decoys": []}
    table = ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
             "| s.md | Total Signups | 4217 | 4127 | drift |\n")
    assert score_findings(table, key)["recall"] == 1.0


def test_genuinely_different_values_still_fail_to_match():
    """正規化は緩めても、値が本当に違えば一致させない。"""
    key = {"defects": [{"location": "s.md", "key": "total_signups",
                        "expected": "4217", "actual": "4127"}], "decoys": []}
    table = ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
             "| s.md | total_signups | 4217 | 9999 | drift |\n")
    assert score_findings(table, key)["recall"] == 0.0


# --- 値の一致は「表記」ではなく「実質」で見る (パイロット第2の発見) -----------

def _key(actual, expected="x"):
    return {"defects": [{"location": "s.md", "key": "k",
                         "expected": expected, "actual": actual}], "decoys": []}


def _tbl(actual, expected="x"):
    return ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
            f"| s.md | k | {expected} | {actual} | drift |\n")


@pytest.mark.parametrize("reported,truth", [
    ("42%", "42"),
    ("about USD 1,300", "1300"),
    ("3x", "3"),
    ("99.95%", "99.95"),
])
def test_numeric_values_match_regardless_of_units_and_separators(reported, truth):
    """単位や記号の違いで正解を落とさない。

    実 run で、7 項目すべてを正しく判定した artifact が recall 0.2 になった。
    `42%` と `42` を別物と扱っていたため。測っているのは判定であって表記ではない。
    """
    assert score_findings(_tbl(reported), _key(truth))["recall"] == 1.0


def test_numerically_different_values_still_fail():
    assert score_findings(_tbl("9999"), _key("4127"))["recall"] == 0.0


def test_textual_values_match_by_containment():
    """自由記述の値は包含で一致させる。"""
    truth = "improved every single week"
    assert score_findings(_tbl('"improved every single week"'), _key(truth))["recall"] == 1.0


def test_unrelated_text_does_not_match():
    assert score_findings(_tbl("something else entirely"), _key("improved every single week"))["recall"] == 0.0


def test_marking_everything_as_drift_does_not_reach_full_score():
    """撃ちまくりが満点にならないこと (判定だけを見ると全部 drift が有利になる)。"""
    key = {"defects": [{"location": "s.md", "key": "a", "expected": "1", "actual": "2"}],
           "decoys": [{"location": "s.md", "key": "b", "note": "compliant"},
                      {"location": "s.md", "key": "c", "note": "compliant"}]}
    table = ("| location | key | expected | actual | verdict |\n|---|---|---|---|---|\n"
             "| s.md | a | 1 | 2 | drift |\n"
             "| s.md | b | 1 | 1 | drift |\n"
             "| s.md | c | 1 | 1 | drift |\n")
    result = score_findings(table, key)
    assert result["recall"] == 1.0
    assert result["precision"] < 0.5
    assert result["f1"] < 0.7
