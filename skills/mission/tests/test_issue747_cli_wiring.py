"""#747 項目 2: 本番の入口が plan 経路を使うこと.

部品と entry point が揃っていても、本番から呼ばれなければ retry は起きない。
第 1 段で「関数はあるが本番から呼ばれていない」型の欠陥を 2 度指摘された。
"""
import pytest


def test_the_run_helper_prefers_the_plan_route():
    """`run_context_manifest` must reach the retrying entry point."""
    from pathlib import Path

    import mission_application.evidence as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "execute_retry_safe_evidence_plan" in source


def test_the_plan_route_is_used_when_the_repository_supports_it(tmp_path):
    """A repository that offers the entry point gets the plan, not a callback."""
    from mission_application.evidence import ContextManifestRequest, run_context_manifest

    seen = []

    class _Repository:
        def execute_retry_safe_evidence_plan(self, plan):
            seen.append(plan)
            raise _Stop()

        def execute_evidence_transition_effects(self, prepare):  # pragma: no cover
            raise AssertionError("the callback route was taken")

    with pytest.raises(_Stop):
        run_context_manifest(
            ContextManifestRequest(
                now="2026-01-01T00:00:00Z",
                iteration=1,
                publication_path="build/m.json",
                project_root=tmp_path,
            ),
            _Repository(),
        )
    assert seen and seen[0].publication_path == "build/m.json"


def test_a_repository_without_the_entry_point_still_works(tmp_path):
    """Retained v4 has no plan route; it must keep the single-shot one."""
    from mission_application.evidence import ContextManifestRequest, run_context_manifest

    seen = []

    class _Legacy:
        def execute_evidence_transition_effects(self, prepare):
            seen.append(prepare)
            raise _Stop()

    with pytest.raises(_Stop):
        run_context_manifest(
            ContextManifestRequest(
                now="2026-01-01T00:00:00Z",
                iteration=1,
                publication_path="build/m.json",
                project_root=tmp_path,
            ),
            _Legacy(),
        )
    assert seen, "the callback route was lost"


class _Stop(Exception):
    """End the run once the route has been observed."""
