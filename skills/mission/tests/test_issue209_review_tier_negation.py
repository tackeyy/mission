"""Issue #209: review tier actual-operation signals understand local context.

The public ``derive_review_tier`` tuple and ``review_tier_signals`` string list
remain backward compatible.  Additive decision details explain every matched
signal, including actual-operation candidates suppressed by a clear negation.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue209", MISSION_STATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decision(mission: str, complexity: str = "Simple", risk: str | None = None):
    return _load_module().derive_review_tier_decision(mission, complexity, risk)


def _details(decision: dict, *, category: str = "actual-operation") -> list[dict]:
    return [item for item in decision["signal_details"] if item["category"] == category]


def test_affirmative_actual_operation_is_kept_with_provenance():
    decision = _decision("本番へ deploy する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    detail = next(item for item in _details(decision) if item["keyword"] == "deploy")
    assert detail["decision"] == "included"
    assert detail["reason"] == "affirmative-actual-operation"
    assert detail["match"] == "deploy"
    assert "deploy" in detail["context"]
    assert detail["source"] == "mission_text"
    assert decision["mission_text"][detail["start"] : detail["end"]] == detail["match"]


def test_clearly_negated_actual_operation_is_suppressed():
    decision = _decision("本番への deploy は実行しない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert _details(decision)
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}
    assert {item["reason"] for item in _details(decision)} == {"negated-actual-operation"}


def test_double_negation_stays_conservative():
    decision = _decision("本番へ deploy しないわけではない")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "uncertain-or-double-negation" for item in _details(decision))


def test_conditional_actual_operation_stays_conservative():
    decision = _decision("必要なら本番へ deploy する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "conditional-or-uncertain-context" for item in _details(decision))


def test_quoted_operation_without_explicit_non_execution_stays_conservative():
    decision = _decision("手順書には「本番へ deploy する」と書かれている")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "quoted-context-conservative" for item in _details(decision))


def test_quoted_operation_is_suppressed_only_when_quote_only_intent_is_explicit():
    decision = _decision("「本番へ deploy する」という文言を手順書に引用する")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert _details(decision)
    assert {item["reason"] for item in _details(decision)} == {"quoted-non-operation"}


def test_explicit_execution_overrides_quoted_example():
    decision = _decision("手順書の「本番へ deploy する」を実際に実行する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["decision"] == "included" for item in _details(decision))


def test_distant_negation_does_not_suppress_a_separate_actual_operation():
    decision = _decision("データ削除は行わない。代わりに本番へ deploy する")

    assert decision["tier"] == "full"
    deleted = [item for item in _details(decision) if item["keyword"] == "データ削除"]
    deployed = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert deleted and {item["decision"] for item in deleted} == {"suppressed"}
    assert deployed and {item["decision"] for item in deployed} == {"included"}
    assert "irreversible-keyword:deploy" in decision["signals"]


def test_global_explicit_non_operation_statement_suppresses_prior_candidate():
    decision = _decision("deploy 手順を調査する。実操作は行わない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}
    assert {item["reason"] for item in _details(decision)} == {"global-explicit-non-operation"}


def test_every_occurrence_is_evaluated_when_same_keyword_has_mixed_intent():
    decision = _decision("deploy しないが、deploy する")

    deploy_details = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert decision["tier"] == "full"
    assert decision["signals"] == ["irreversible-keyword:deploy"]
    assert [item["decision"] for item in deploy_details] == ["suppressed", "included"]
    assert [item["match"] for item in deploy_details] == ["deploy", "deploy"]


@pytest.mark.parametrize(
    "mission",
    [
        "本番へ deploy する可能性がある",
        "本番へ deploy するかもしれない",
        "本番へ deploy するかは未確定",
    ],
)
def test_uncertain_context_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "conditional-or-uncertain-context" for item in _details(decision))


@pytest.mark.parametrize(
    ("mission", "expected_tier", "expected_reason"),
    [
        ("do not deploy to production", "light", "negated-actual-operation"),
        ("deployment is not impossible", "full", "uncertain-or-double-negation"),
        ("deploy only if approval is granted", "full", "conditional-or-uncertain-context"),
        ('the guide says "deploy to production"', "full", "quoted-context-conservative"),
    ],
)
def test_english_negation_uncertainty_condition_and_quote(
    mission, expected_tier, expected_reason
):
    decision = _decision(mission)

    assert decision["tier"] == expected_tier
    assert expected_reason in {item["reason"] for item in _details(decision)}


@pytest.mark.parametrize("mission", ["deploy せず調査だけ行う", "deploy を禁止する", "deploy は対象外とする"])
def test_japanese_explicit_non_operation_variants_are_suppressed(mission):
    decision = _decision(mission)

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["reason"] for item in _details(decision)} == {"negated-actual-operation"}


def test_only_actual_operation_signals_are_suppressed_by_negation():
    decision = _decision("API token は更新しない")

    assert decision["tier"] == "full"
    assert "security-keyword:api token" in decision["signals"]
    security = _details(decision, category="security")
    assert security and {item["decision"] for item in security} == {"included"}
    assert {item["reason"] for item in security} == {"security-context-conservative"}


def test_base_critical_and_high_risk_escalators_are_never_downgraded():
    critical = _decision("本番への deploy は実行しない", complexity="Critical")
    high_risk = _decision("small refactor", risk="high")

    assert critical["base_tier"] == "full"
    assert critical["tier"] == "full"
    assert critical["signals"] == []
    assert high_risk["tier"] == "full"
    assert high_risk["signals"] == ["task_profile.risk=high"]
    assert any(
        item["reason"] == "task-profile-high-risk" and item["decision"] == "included"
        for item in high_risk["signal_details"]
    )


def test_existing_tuple_api_and_string_signal_list_remain_compatible():
    module = _load_module()

    tier, signals = module.derive_review_tier("deploy to production", "Simple")

    assert tier == "full"
    assert signals == ["irreversible-keyword:deploy", "irreversible-keyword:production"]
    assert all(isinstance(signal, str) for signal in signals)


def test_legacy_signal_order_and_same_keyword_deduplication_are_preserved():
    module = _load_module()

    tier, signals = module.derive_review_tier(
        "production deploy, then deploy; API token and secret", "Simple"
    )

    assert tier == "full"
    assert signals == [
        "irreversible-keyword:deploy",
        "irreversible-keyword:production",
        "security-keyword:secret",
        "security-keyword:api token",
    ]


def test_init_persists_additive_signal_details_for_audit(run_cli, tmp_path):
    result = run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert state["review_tier"] == "light"
    assert state["review_tier_signals"] == []
    assert all(isinstance(signal, str) for signal in state["review_tier_signals"])
    details = state["review_tier_signal_details"]
    assert details and {item["decision"] for item in details} == {"suppressed"}
    assert all(
        {
            "signal",
            "category",
            "keyword",
            "match",
            "context",
            "decision",
            "reason",
            "source",
            "start",
            "end",
        }
        <= item.keys()
        for item in details
    )


def test_complexity_rederive_refreshes_additive_signal_details(run_cli, tmp_path):
    run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("set", "complexity=Standard", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["review_tier"] == "standard"
    assert state["review_tier_signals"] == []
    assert state["review_tier_signal_details"]
    assert {item["reason"] for item in state["review_tier_signal_details"]} == {
        "negated-actual-operation"
    }


def test_get_exposes_additive_signal_details_as_public_audit_output(run_cli, tmp_path):
    run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("get", cwd=tmp_path, check=True)
    output = json.loads(result.stdout)

    assert output["review_tier_signal_details"]
    assert output["review_tier_signal_details"][0]["decision"] == "suppressed"


def test_user_override_preserves_auto_signal_audit_meaning(run_cli, tmp_path):
    run_cli(
        "init", "deploy to production", "--complexity", "Simple", cwd=tmp_path, check=True
    )
    before = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    run_cli("set", "review_tier=light", cwd=tmp_path, check=True)
    after = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert after["review_tier_source"] == "user"
    assert after["review_tier_signal_details"] == before["review_tier_signal_details"]
    assert after["review_tier_signals"] == before["review_tier_signals"]


def test_legacy_state_without_details_supports_set_next_and_get(run_cli, state_dir):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text())
    state["mission"] = "本番への deploy は実行しない"
    state["review_tier"] = "full"
    state["review_tier_source"] = "auto"
    state["review_tier_signals"] = ["irreversible-keyword:deploy"]
    state.pop("review_tier_signal_details", None)
    state_file.write_text(json.dumps(state))

    assert run_cli("get", cwd=state_dir.parent).returncode == 0
    assert run_cli("next", cwd=state_dir.parent).returncode == 0
    result = run_cli("set", "complexity=Simple", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    updated = json.loads(state_file.read_text())
    assert updated["review_tier"] == "light"
    assert updated["review_tier_signals"] == []
    assert updated["review_tier_signal_details"]


def test_adversarial_long_mission_is_evaluated_without_regex_blowup():
    mission = ("ordinary planning context; " * 8_000) + "do not deploy"

    started = time.perf_counter()
    decision = _decision(mission)
    elapsed = time.perf_counter() - started

    assert decision["tier"] == "light"
    assert elapsed < 2.0
