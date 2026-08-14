"""Issue #482: review tier boundary corpus regression tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
BOUNDARY_CORPUS = Path(__file__).resolve().parent / "fixtures" / "review_tier_boundary_corpus.json"
_MODULE = None


def _load_module():
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    spec = importlib.util.spec_from_file_location("mission_state_issue482", MISSION_STATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def _load_cases() -> list[dict]:
    data = json.loads(BOUNDARY_CORPUS.read_text())
    cases: list[dict] = []
    for language in ("english", "japanese"):
        for bucket in ("positive", "negative"):
            for case in data[language][bucket]:
                if case.get("category", "lexical-boundary") == "lexical-boundary":
                    cases.append(case)
    return cases


CASES = _load_cases()


def _decision(text: str) -> dict:
    return _load_module().derive_review_tier_decision(text, "Simple")


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_boundary_corpus_expected_signal_hit(case):
    decision = _decision(case["text"])
    signal_hit = bool(decision["signals"])

    assert signal_hit == case["expected_signal_hit"], case["id"]
    assert (decision["tier"] == "full") == case["expected_signal_hit"], case["id"]
    assert all(item["source"] == "mission_text" for item in decision["signal_details"] if item["start"] is not None)
    assert all(
        decision["mission_text"][item["start"] : item["end"]] == item["match"]
        for item in decision["signal_details"]
        if item["start"] is not None
    )
    if case["expected_signal_hit"]:
        assert any(item["decision"] == "included" for item in decision["signal_details"])
    else:
        assert not any(item["decision"] == "included" for item in decision["signal_details"])


@pytest.mark.parametrize(
    ("case_id", "expected_match"),
    [
        ("en-pos-004", "deploying"),
        ("ja-pos-007", "鍵"),
    ],
    ids=["en-pos-004", "ja-pos-007"],
)
def test_boundary_corpus_signal_details_align_with_match_spans(case_id, expected_match):
    case = next(item for item in CASES if item["id"] == case_id)
    decision = _decision(case["text"])
    included = next(item for item in decision["signal_details"] if item["decision"] == "included")

    assert included["match"] == expected_match
    assert included["source"] == "mission_text"
    assert decision["mission_text"][included["start"] : included["end"]] == included["match"]
