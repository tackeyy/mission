"""plugins/mission 配下の同期対象ファイルが skills/ 正典と一致することを確認する.

同期対象:
  scripts/mission-stop-guard.sh
  scripts/mission-local-authoring-sync.sh
  scripts/mission-audit.py
  skills/mission/bin/mission-state.py
  skills/mission/lib/activity_segments.py
  skills/mission/lib/audit_findings.py
  skills/mission/lib/mission_common.py
  skills/mission/lib/provider_eligibility.py
  skills/mission/lib/planning_provider_metrics.py
  skills/mission/lib/provider_public_contract.py
  skills/mission/lib/provider_preflight.py
  skills/mission/lib/specialist_lifecycle.py
  skills/mission/refs/specialist-registry.md (存在する場合)
  skills/mission/refs/self-improvement.md
  skills/mission/refs/changelog.md
  skills/mission/refs/state-management.md
  skills/mission/refs/codex-setup.md
  skills/mission/SKILL.md
  skills/mission-planner/SKILL.md
  skills/mission-critic/SKILL.md
  skills/mission-reviewer/SKILL.md
  skills/mission-scorer/SKILL.md

対応する plugins 側パス:
  plugins/mission/scripts/mission-stop-guard.sh
  plugins/mission/scripts/mission-local-authoring-sync.sh
  plugins/mission/scripts/mission-audit.py
  plugins/mission/skills/mission/bin/mission-state.py
  plugins/mission/skills/mission/lib/activity_segments.py
  plugins/mission/skills/mission/lib/audit_findings.py
  plugins/mission/skills/mission/lib/mission_common.py
  plugins/mission/skills/mission/lib/provider_eligibility.py
  plugins/mission/skills/mission/lib/planning_provider_metrics.py
  plugins/mission/skills/mission/lib/provider_public_contract.py
  plugins/mission/skills/mission/lib/provider_preflight.py
  plugins/mission/skills/mission/lib/specialist_lifecycle.py
  plugins/mission/skills/mission/refs/specialist-registry.md (存在する場合)
  plugins/mission/skills/mission/refs/self-improvement.md
  plugins/mission/skills/mission/refs/changelog.md
  plugins/mission/skills/mission/refs/state-management.md
  plugins/mission/skills/mission/refs/codex-setup.md
  plugins/mission/skills/mission/SKILL.md
  plugins/mission/skills/mission-planner/SKILL.md
  plugins/mission/skills/mission-critic/SKILL.md
  plugins/mission/skills/mission-reviewer/SKILL.md
  plugins/mission/skills/mission-scorer/SKILL.md

同期コマンド:
  cp scripts/mission-stop-guard.sh        plugins/mission/scripts/mission-stop-guard.sh
  cp scripts/mission-audit.py             plugins/mission/scripts/mission-audit.py
  cp skills/mission/bin/mission-state.py  plugins/mission/skills/mission/bin/mission-state.py
  cp skills/mission/lib/activity_segments.py plugins/mission/skills/mission/lib/activity_segments.py
  cp skills/mission/lib/audit_findings.py plugins/mission/skills/mission/lib/audit_findings.py
  cp skills/mission/lib/mission_common.py plugins/mission/skills/mission/lib/mission_common.py
  cp skills/mission/lib/provider_eligibility.py plugins/mission/skills/mission/lib/provider_eligibility.py
  cp skills/mission/lib/provider_public_contract.py plugins/mission/skills/mission/lib/provider_public_contract.py
  cp skills/mission/lib/provider_preflight.py plugins/mission/skills/mission/lib/provider_preflight.py
  cp skills/mission/lib/specialist_lifecycle.py plugins/mission/skills/mission/lib/specialist_lifecycle.py
  cp skills/mission/refs/specialist-registry.md plugins/mission/skills/mission/refs/specialist-registry.md
  cp skills/mission/refs/self-improvement.md plugins/mission/skills/mission/refs/self-improvement.md
  cp skills/mission/refs/changelog.md plugins/mission/skills/mission/refs/changelog.md
  cp skills/mission/refs/state-management.md plugins/mission/skills/mission/refs/state-management.md
  cp skills/mission/SKILL.md              plugins/mission/skills/mission/SKILL.md
  cp skills/mission-planner/SKILL.md      plugins/mission/skills/mission-planner/SKILL.md
  cp skills/mission-critic/SKILL.md       plugins/mission/skills/mission-critic/SKILL.md
  cp skills/mission-reviewer/SKILL.md     plugins/mission/skills/mission-reviewer/SKILL.md
  cp skills/mission-scorer/SKILL.md       plugins/mission/skills/mission-scorer/SKILL.md
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]  # mission-selfheal/

SYNC_PAIRS = [
    (
        REPO_ROOT / "scripts" / "mission-stop-guard.sh",
        REPO_ROOT / "plugins" / "mission" / "scripts" / "mission-stop-guard.sh",
    ),
    (
        REPO_ROOT / "scripts" / "mission-audit.py",
        REPO_ROOT / "plugins" / "mission" / "scripts" / "mission-audit.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "specialist-registry.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "specialist-registry.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "self-improvement.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "self-improvement.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "changelog.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "changelog.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-planner" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-planner" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-critic" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-critic" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-reviewer" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-scorer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-scorer" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "state-management.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "state-management.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "provider_eligibility.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "provider_eligibility.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "error_guidance.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "error_guidance.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "planning_lifecycle.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "planning_lifecycle.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "planning_provider_metrics.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "planning_provider_metrics.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "review_learning.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "review_learning.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "provider_public_contract.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "provider_public_contract.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "artifact_contract.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "artifact_contract.py",
    ),
    (
        REPO_ROOT / "skills" / "mission-executor" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-executor" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-executor" / "refs" / "artifact-handoff.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-executor" / "refs" / "artifact-handoff.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "state_snapshot.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "state_snapshot.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "worktree_archive.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "worktree_archive.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "activity_segments.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "activity_segments.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "audit_findings.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "audit_findings.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "mission_common.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "mission_common.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "scoring_provenance.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "scoring_provenance.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "specialist_lifecycle.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "specialist_lifecycle.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "provider_preflight.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "provider_preflight.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "plan_contract.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "plan_contract.py",
    ),
]

MISSION_STATE_DISTRIBUTION_MARKERS = [
    "specialist accounting required before pass",
    "PREPARATION_ONLY_MARKERS",
    "_classify_command_provider_result",
]

COMMAND_OUTCOME_SYNC_PAIR = (
    REPO_ROOT / "skills" / "mission" / "lib" / "command_outcomes.py",
    REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "command_outcomes.py",
)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _sync_pair_for(canonical_relative_path: str) -> tuple[Path, Path]:
    canonical = REPO_ROOT / canonical_relative_path
    matches = [pair for pair in SYNC_PAIRS if pair[0] == canonical]
    assert len(matches) == 1, (
        f"expected one sync inventory entry for {canonical_relative_path}, "
        f"found {len(matches)}"
    )
    return matches[0]


def _assert_optional_pair_in_sync(src: Path, dst: Path, label: str):
    if not src.exists() and not dst.exists():
        return
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"{label} が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_stop_guard_in_sync():
    """scripts/mission-stop-guard.sh と plugins/mission/scripts/mission-stop-guard.sh が一致する."""
    src, dst = SYNC_PAIRS[0]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-stop-guard.sh が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_command_outcomes_py_in_sync():
    """Shared command outcome reader is shipped with the plugin mirror."""
    src, dst = COMMAND_OUTCOME_SYNC_PAIR
    assert src.exists() and dst.exists()
    assert _md5(src) == _md5(dst)


def test_local_authoring_bootstrap_files_in_sync():
    """Local authoring bootstrap script and setup reference match the plugin mirror."""
    pairs = (
        (
            REPO_ROOT / "scripts" / "mission-local-authoring-sync.sh",
            REPO_ROOT / "plugins" / "mission" / "scripts" / "mission-local-authoring-sync.sh",
        ),
        (
            REPO_ROOT / "skills" / "mission" / "refs" / "codex-setup.md",
            REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "codex-setup.md",
        ),
    )
    for src, dst in pairs:
        assert src.exists(), f"canonical file does not exist: {src}"
        assert dst.exists(), f"plugin mirror does not exist: {dst}"
        assert _md5(src) == _md5(dst), (
            f"local authoring bootstrap file is not synchronized:\n"
            f"  canonical: {src}\n"
            f"  plugin: {dst}\n"
            f"  sync command: bash scripts/sync-codex-plugin-wrapper.sh"
        )


def test_mission_state_py_in_sync():
    """skills/mission/bin/mission-state.py と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[2]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-state.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_activity_segments_py_in_sync():
    """Shared activity timing reducer is identical in the distribution mirror."""
    src, dst = SYNC_PAIRS[-3]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"activity_segments.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_worktree_archive_py_in_sync():
    """Shared immutable archive validator is identical in the distribution mirror."""
    src, dst = SYNC_PAIRS[-4]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"worktree_archive.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_state_snapshot_py_in_sync():
    """Explicit state snapshot contract is identical in the distribution mirror."""
    src, dst = SYNC_PAIRS[-5]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"state_snapshot.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_provider_eligibility_py_in_sync_and_importable():
    """Planning eligibility contract is inventoried and importable from the plugin."""
    src, dst = _sync_pair_for("skills/mission/lib/provider_eligibility.py")
    assert src.exists(), f"canonical file does not exist: {src}"
    assert dst.exists(), f"plugin mirror does not exist: {dst}"
    assert _md5(src) == _md5(dst), (
        "provider_eligibility.py is not synchronized; run the plugin sync script"
    )
    spec = importlib.util.spec_from_file_location("plugin_provider_eligibility", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.normalize_selection_source("auto")
    assert result["selection_source"] == "automatic"


def test_provider_public_contract_py_in_sync_and_importable():
    """Public provider-state hygiene ships with every CLI and audit consumer."""
    src, dst = _sync_pair_for("skills/mission/lib/provider_public_contract.py")
    assert src.exists(), f"canonical file does not exist: {src}"
    assert dst.exists(), f"plugin mirror does not exist: {dst}"
    assert _md5(src) == _md5(dst), (
        "provider_public_contract.py is not synchronized; run the plugin sync script"
    )
    spec = importlib.util.spec_from_file_location("plugin_provider_public_contract", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.validate_specialist_public_state({"specialists_candidates": []})


def test_specialist_lifecycle_py_in_sync_and_importable():
    """The checkpoint identity and transition validator ship in the plugin mirror."""
    src, dst = _sync_pair_for("skills/mission/lib/specialist_lifecycle.py")
    assert src.exists() and dst.exists()
    assert _md5(src) == _md5(dst)
    spec = importlib.util.spec_from_file_location("plugin_specialist_lifecycle", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.new_selection_id().startswith("sel_")


def test_planning_lifecycle_py_in_sync_and_importable():
    src, dst = _sync_pair_for("skills/mission/lib/planning_lifecycle.py")
    assert src.exists() and dst.exists() and _md5(src) == _md5(dst)
    spec = importlib.util.spec_from_file_location("plugin_planning_lifecycle", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None; spec.loader.exec_module(module)
    assert module.derive_planning_lifecycle({"phase": "planning"})["mode"] == "legacy-core"


def test_planning_provider_metrics_py_in_sync_and_importable():
    """The versioned KPI reducer ships with the plugin consumer imports."""
    src, dst = _sync_pair_for("skills/mission/lib/planning_provider_metrics.py")
    assert src.exists() and dst.exists() and _md5(src) == _md5(dst)
    spec = importlib.util.spec_from_file_location("plugin_planning_provider_metrics", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.reduce_planning_provider_kpis([], population_kind="observed")["schema"] == "mission-planning-provider-kpi/1"


def test_review_learning_py_in_sync_and_importable():
    src, dst = _sync_pair_for("skills/mission/lib/review_learning.py")
    assert src.exists() and dst.exists() and _md5(src) == _md5(dst)
    spec = importlib.util.spec_from_file_location("plugin_review_learning", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.learning_identity("execution", "validate every boundary").startswith("sha256:")


def test_plugin_mirror_specialist_recommend_cli_smoke(tmp_path):
    """The distributed CLI imports its mirrored provider contract in a real process."""
    cli = REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py"
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "specialists",
            "recommend",
            "--no-default-skill-roots",
            "--task",
            "Update README documentation",
            "--installed-skills",
            "documentation-provider",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["specialists_candidates"][0]["provider_id"] == "documentation-provider"


def test_audit_findings_py_in_sync():
    """Shared finding period classifier is identical in the distribution mirror."""
    src, dst = SYNC_PAIRS[-2]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"audit_findings.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_common_py_in_sync():
    """Shared state identity and dedupe rank are identical in the mirror."""
    src, dst = SYNC_PAIRS[-1]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission_common.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_state_distribution_contains_specialist_accounting_guards():
    """配布 wrapper が specialist accounting/result-contract gate を欠落させない."""
    src, dst = SYNC_PAIRS[2]
    for path in (src, dst):
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in MISSION_STATE_DISTRIBUTION_MARKERS if marker not in text]
        assert not missing, f"{path} is missing distribution-critical markers: {missing}"


def test_state_management_reference_in_sync():
    """worktree archive を含む state management reference が配布 wrapper と一致する."""
    src, dst = SYNC_PAIRS[11]
    _assert_optional_pair_in_sync(src, dst, "state-management.md")


def test_artifact_contract_distribution_files_in_sync():
    """Artifact validator and executor handoff contract are shipped together."""
    for relative_path, label in (
        ("skills/mission/lib/artifact_contract.py", "artifact_contract.py"),
        ("skills/mission-executor/SKILL.md", "mission-executor/SKILL.md"),
        (
            "skills/mission-executor/refs/artifact-handoff.md",
            "mission-executor/refs/artifact-handoff.md",
        ),
    ):
        src, dst = _sync_pair_for(relative_path)
        _assert_optional_pair_in_sync(src, dst, label)


def test_skill_md_in_sync():
    """skills/mission/SKILL.md と plugins/mission/skills/mission/SKILL.md が一致する."""
    src, dst = SYNC_PAIRS[6]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_planner_skill_md_in_sync():
    """skills/mission-planner/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[7]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-planner/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_critic_skill_md_in_sync():
    """skills/mission-critic/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[8]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-critic/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_reviewer_skill_md_in_sync():
    """skills/mission-reviewer/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[9]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-reviewer/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_scorer_skill_md_in_sync():
    """skills/mission-scorer/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[10]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-scorer/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_audit_py_in_sync():
    """scripts/mission-audit.py と plugins/mission/scripts/mission-audit.py が一致する."""
    src, dst = SYNC_PAIRS[1]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-audit.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_specialist_registry_md_in_sync_when_present():
    """specialist-registry.md は作成済みの場合だけ plugins/mission 側との一致を確認する."""
    src, dst = SYNC_PAIRS[3]
    _assert_optional_pair_in_sync(src, dst, "specialist-registry.md")


def test_self_improvement_md_in_sync():
    """skills/mission/refs/self-improvement.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[4]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"self-improvement.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_changelog_md_in_sync():
    """skills/mission/refs/changelog.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[5]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"changelog.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )
