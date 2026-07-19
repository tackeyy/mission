"""CI品質ゲートを維持したコスト最適化の回帰テスト。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
DEPENDABOT = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")


def test_ci_is_one_bounded_quality_job():
    jobs = CI.split("\njobs:\n", 1)[1]
    assert len(re.findall(r"^  [a-z][a-z0-9-]+:\n", jobs, re.MULTILINE)) == 1
    assert "quality:" in CI
    assert CI.count("actions/checkout@") == 1
    assert CI.count("actions/setup-python@") == 1
    assert "timeout-minutes:" in CI


def test_python_and_shell_quality_gates_remain():
    assert "python -m pytest -q skills/mission" in CI
    assert "shellcheck scripts/mission-stop-guard.sh scripts/sync-codex-plugin-wrapper.sh" in CI
    assert "apt-get" not in CI
    assert "cache: pip" in CI
    assert "requirements-ci.txt" in CI


def test_python_scope_includes_all_files_read_by_the_test_suite():
    for required in (
        "file.startsWith('skills/')",
        "file.startsWith('plugins/mission/')",
        "file.startsWith('docs/')",
        "file.startsWith('benchmarks/mission-vs-goal/')",
        "file === 'README.md'",
        "file === '.github/requirements-ci.txt'",
    ):
        assert required in CI


def test_stale_prs_are_cancelled_and_ready_prs_run_full_ci():
    assert "ready_for_review" in CI
    assert "concurrency:" in CI
    assert "github.event.pull_request.number" in CI
    assert "cancel-in-progress: true" in CI


def test_dependabot_updates_are_batched_and_not_automatically_rebased():
    assert "groups:" in DEPENDABOT
    assert "cooldown:" in DEPENDABOT
    assert "rebase-strategy: disabled" in DEPENDABOT
