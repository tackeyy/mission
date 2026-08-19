"""CI品質ゲートを維持したコスト最適化の回帰テスト。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
DEPENDABOT = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")


# CI の job 形状は「許可リスト」で固定する。job を無制限に増やせば無駄が戻り、
# 減らせば壁時間が戻るため、増減のどちらも意図的な契約改定を強制する。
JOB_CONTRACT = {
    "shell": {"runs-on": "ubuntu-latest", "timeout-minutes": 5},
    "test": {"runs-on": "ubuntu-latest", "timeout-minutes": 15},
    "quality": {"runs-on": "ubuntu-latest", "timeout-minutes": 5},
}
REQUIRED_STATUS_CHECK = "quality"
SHARD_TOTAL = 4


def _job_names():
    jobs = CI.split("\njobs:\n", 1)[1]
    return re.findall(r"^  ([a-z][a-z0-9-]*):\n", jobs, re.MULTILINE)


def test_ci_job_shape_matches_the_contract():
    assert _job_names() == list(JOB_CONTRACT)
    for job, expected in JOB_CONTRACT.items():
        body = re.search(rf"^  {job}:\n(.*?)(?=^  [a-z]|\Z)", CI, re.MULTILINE | re.DOTALL).group(1)
        assert f"runs-on: {expected['runs-on']}" in body, job
        assert f"timeout-minutes: {expected['timeout-minutes']}" in body, job


def test_test_job_is_sharded_across_independent_runners():
    assert f"SHARD_TOTAL: '{SHARD_TOTAL}'" in CI
    assert f"shard: [{', '.join(str(i) for i in range(1, SHARD_TOTAL + 1))}]" in CI
    assert "fail-fast: false" in CI  # 1 シャードの赤で他シャードの失敗を隠さない
    assert (
        'run: make test-shard PYTHON=python SHARD_INDEX=${{ matrix.shard }} '
        'SHARD_TOTAL=${{ env.SHARD_TOTAL }} '
        'PYTEST_TARGETS="${{ steps.changes.outputs.python_targets }}"'
    ) in CI


def test_quality_is_the_single_fail_closed_aggregation_gate():
    """required status check は集約 job 1 本。上流が success 以外なら必ず落ちる。"""
    body = re.search(r"^  quality:\n(.*?)\Z", CI, re.MULTILINE | re.DOTALL).group(1)
    assert "needs: [shell, test]" in body
    assert "if: always()" in body  # 上流が落ちても集約 job 自体は必ず走る
    assert 'if [ "${entry#*=}" != "success" ]; then' in body
    assert "SHELL_RESULT: ${{ needs.shell.result }}" in body
    assert "TEST_RESULT: ${{ needs.test.result }}" in body
    assert 'exit "${failed}"' in body


def test_draft_prs_do_not_consume_ci():
    assert CI.count("github.event.pull_request.draft == false") == len(JOB_CONTRACT)


def test_python_and_shell_quality_gates_remain():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYTEST_TARGETS ?= skills/mission" in makefile
    assert "$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile $(PYTEST_TARGETS)" in makefile
    assert "$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile $$targets" in makefile  # test-shard も並列維持
    assert "$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile skills/mission -k" in makefile  # test-e2e も並列維持
    assert (
        "shellcheck scripts/mission-stop-guard.sh plugins/mission/scripts/mission-stop-guard.sh "
        "scripts/sync-codex-plugin-wrapper.sh scripts/mission-local-authoring-sync.sh"
    ) in CI
    assert "apt-get" not in CI
    assert "cache: pip" in CI
    assert "requirements-ci.txt" in CI


def test_shellcheck_runs_unconditionally_and_never_waits_on_the_python_suite():
    """shellcheck は 0 秒で終わるため、条件分岐で節約する対象ではない。

    テストと同一 job に置くと shell の誤りが 15 分後にしか出ないため、
    独立 job として並列に走らせる。
    """
    shell_body = re.search(r"^  shell:\n(.*?)(?=^  test:)", CI, re.MULTILINE | re.DOTALL).group(1)
    assert "shellcheck --version" in shell_body
    assert "actions/setup-python@" not in shell_body
    assert "steps.changes.outputs" not in shell_body  # 変更スコープに依存しない


def test_shard_selection_is_exhaustive_disjoint_and_fail_closed():
    """シャード分割スクリプトが品質ゲートを空洞化しないことを CI 契約側でも固定する。"""
    shard_script = (ROOT / "scripts" / "ci_shard_targets.py").read_text(encoding="utf-8")
    assert "ordered[index - 1 :: total]" in shard_script
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "set -eu" in makefile  # 分割スクリプトの失敗を無引数 pytest へ落とさない
    assert 'test -n "$$targets"' in makefile


def test_repository_wide_python_suite_runs_for_every_pr():
    assert "const decision = classifyChangedFiles({" in CI
    assert "core.setOutput('python_targets', decision.pythonTargets);" in CI


def test_stale_prs_are_cancelled_and_ready_prs_run_full_ci():
    assert "ready_for_review" in CI
    assert "concurrency:" in CI
    assert "github.event.pull_request.number" in CI
    assert "cancel-in-progress: true" in CI


def test_dependabot_updates_are_batched_and_not_automatically_rebased():
    assert "groups:" in DEPENDABOT
    assert "cooldown:" in DEPENDABOT
    assert "rebase-strategy: disabled" in DEPENDABOT
