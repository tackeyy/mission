"""Issue #100: ミッション本文でスキルを名指し指定しても ask-user に倒れ、log-invocation が reject される.

実測 (2026-07-02 social-foundry 監査 mission / Critical / 9観点スキル明示指定):
recommend が high-risk task profile で decision: ask-user に倒れ、初回 log-invocation 8件が
`--selection-source confirmed-user` 不足で全滅した。ミッション本文でのスキル明示指定は
実質 confirmed-user なので、`recommend --user-specified` で selected として直接記録する。
"""

import json


def _recommend(run_cli, cwd, *extra):
    return run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review production security of the React UI component",  # high-risk (production/security)
        "--installed-skills", "frontend-provider,visual-quality-provider",
        "--json", *extra,
        cwd=cwd,
    )


def _json_result(r):
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_high_risk_without_user_specified_still_asks_user(run_cli, tmp_path):
    """回帰ガード: 名指しなしの high-risk は従来どおり ask-user."""
    data = _json_result(_recommend(run_cli, tmp_path))
    assert data["task_profile"]["risk"] == "high"
    assert data["specialists_decision"]["action"] == "ask-user"


def test_user_specified_skips_ask_user_on_high_risk(run_cli, tmp_path):
    data = _json_result(_recommend(run_cli, tmp_path, "--user-specified", "frontend-provider"))
    d = data["specialists_decision"]
    assert d["policy"] == "user-specified"
    assert d["action"] == "select"
    assert d["prompted_user"] is False
    selected = data["specialists_selected"]
    assert [s["skill"] for s in selected] == ["frontend-provider"]
    assert selected[0]["selection_source"] == "user-specified"


def test_user_specified_multiple_all_selected(run_cli, tmp_path):
    data = _json_result(_recommend(
        run_cli, tmp_path, "--user-specified", "frontend-provider,visual-quality-provider"))
    skills = sorted(s["skill"] for s in data["specialists_selected"])
    assert skills == ["frontend-provider", "visual-quality-provider"]
    assert all(s["selection_source"] == "user-specified" for s in data["specialists_selected"])


def test_user_specified_unknown_skill_falls_back_to_ask_user(run_cli, tmp_path):
    """名指しスキルが候補に無い/未インストールなら従来フロー (high-risk → ask-user) を維持."""
    data = _json_result(_recommend(run_cli, tmp_path, "--user-specified", "not-installed-skill"))
    assert data["specialists_decision"]["action"] == "ask-user"


def test_user_specified_first_use_provider_still_asks(run_cli, tmp_path):
    """安全弁: first-use consent が必要な provider は名指しでも ask-user を維持する."""
    data = _json_result(_recommend(
        run_cli, tmp_path,
        "--user-specified", "frontend-provider",
        "--first-use", "frontend-provider",
    ))
    assert data["specialists_decision"]["action"] == "ask-user"


def test_user_specified_record_state_unblocks_log_invocation(state_dir, run_cli):
    """本丸: --user-specified + --record-state 後は log-invocation が --selection-source なしで通る."""
    r = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review production security of the React UI component",
        "--installed-skills", "frontend-provider",
        "--user-specified", "frontend-provider",
        "--record-state", "--json",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr

    r2 = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "frontend",
        "--skill", "frontend-provider",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        cwd=state_dir.parent,
    )
    assert r2.returncode == 0, f"stderr: {r2.stderr}"
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    assert state["specialists_selected"][0]["selection_source"] == "user-specified"
    assert state["specialist_invocations"][0]["skill"] == "frontend-provider"
