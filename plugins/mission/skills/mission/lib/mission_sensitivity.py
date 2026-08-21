"""#598: 計測器の感度検証。

事前登録した最優先条件は「**差を検出できる計測器であることを先に証明する**」
こと。意図的に劣化させた arm と正常な arm を比較し、F1 差が閾値以上に出るかを
見る。ここを通過しない採点器では判定 run を実施しない。

これは #390 が「テストは green だが実 run では未解決」で close され、3 世代
同じ失敗を繰り返した根本原因への対処である。「差が出なかった」と「差を
検出できない」を区別できなければ、どんな結論も根拠を持たない。
"""
from __future__ import annotations

import json

#: docs/PRE_REGISTRATION.md で事前登録した分離の閾値。
#: **データを見た後に動かさない。** 変更は事前登録の無効化として扱う。
SENSITIVITY_THRESHOLD = 0.15


def load_answer_key(path, task_id):
    """正解キーを読み込む。未登録タスクは KeyError（推測で空を返さない）。"""
    payload = json.loads(path.read_text(encoding="utf-8") if hasattr(path, "read_text")
                         else open(path, encoding="utf-8").read())
    tasks = payload.get("tasks") or {}
    if task_id not in tasks:
        raise KeyError(f"no answer key for task {task_id!r}")
    entry = tasks[task_id]
    return {"defects": entry.get("defects") or [], "decoys": entry.get("decoys") or []}


def reachable_defects(answer_key, readable_fixtures):
    """読める fixture だけで確立できる defect を返す。

    「主張がどこにあるか」(`location`) と「検証に何が要るか」(`evidence`) は
    別物である。集計値の誤りを指摘するには、主張が載っている要約だけでなく
    **元の明細表**が要る。location だけで到達可能性を数えると、劣化 arm が
    実際には検出不能な欠陥を「検出できるはず」と誤判定する。
    """
    readable = {str(path).rsplit("/", 1)[-1] for path in readable_fixtures}
    out = []
    for defect in answer_key.get("defects") or []:
        needed = defect.get("evidence") or [defect.get("location")]
        if all(str(name).rsplit("/", 1)[-1] in readable for name in needed if name):
            out.append(defect)
    return out


def build_degraded_prompt_suffix(readable_fixtures):
    """劣化 arm 用のプロンプト断片を作る。

    劣化は「**証拠へのアクセス制限**」で作る。「手を抜け」と指示すると劣化の
    度合いがモデル任せになり再現しない。読める fixture を減らせば、
    **発見できない欠陥が確定的に決まる**ため、期待される差が事前に分かる。
    """
    if not readable_fixtures:
        raise ValueError("degraded arm still needs at least one readable fixture")
    listed = "\n".join(f"- {path}" for path in readable_fixtures)
    return (
        "\nEvidence restriction for this run:\n"
        "- Read only the following files. Do not open any other fixture.\n"
        f"{listed}\n"
        "- Report findings only from what these files support.\n"
    )


def _scored(results):
    values = []
    unscored = 0
    for result in results or []:
        value = (result or {}).get("f1")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
        else:
            unscored += 1
    return values, unscored


def evaluate_sensitivity(normal, degraded):
    """正常 arm と劣化 arm の F1 差から、計測器の検出力を判定する。"""
    normal_values, normal_unscored = _scored(normal)
    degraded_values, degraded_unscored = _scored(degraded)

    normal_mean = sum(normal_values) / len(normal_values) if normal_values else None
    degraded_mean = sum(degraded_values) / len(degraded_values) if degraded_values else None

    if normal_mean is None or degraded_mean is None:
        return {
            "normal_mean_f1": normal_mean,
            "degraded_mean_f1": degraded_mean,
            "normal_scored": len(normal_values),
            "degraded_scored": len(degraded_values),
            "normal_unscored": normal_unscored,
            "degraded_unscored": degraded_unscored,
            "separation": None,
            "threshold": SENSITIVITY_THRESHOLD,
            "passes": False,
            "verdict": (
                "no scored results on at least one arm; the instrument cannot be "
                "validated and no comparative run should be started"
            ),
        }

    separation = round(normal_mean - degraded_mean, 6)
    passes = separation >= SENSITIVITY_THRESHOLD
    if passes:
        verdict = (
            f"instrument separates a deliberately degraded arm by {separation:.3f} "
            f"(>= {SENSITIVITY_THRESHOLD}); comparative runs are licensed"
        )
    elif separation < 0:
        verdict = (
            "degraded arm scored higher than the normal arm; the instrument is "
            "measuring something other than quality and cannot detect a difference"
        )
    else:
        verdict = (
            f"separation {separation:.3f} is below {SENSITIVITY_THRESHOLD}; the "
            "instrument cannot detect a difference this large, so a null result "
            "from it would not be evidence of no difference"
        )
    return {
        "normal_mean_f1": round(normal_mean, 6),
        "degraded_mean_f1": round(degraded_mean, 6),
        "normal_scored": len(normal_values),
        "degraded_scored": len(degraded_values),
        "normal_unscored": normal_unscored,
        "degraded_unscored": degraded_unscored,
        "separation": separation,
        "threshold": SENSITIVITY_THRESHOLD,
        "passes": passes,
        "verdict": verdict,
    }
