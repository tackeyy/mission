"""Shared fixtures for mission-state.py tests."""
import json
import os
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
MISSION_LIB = MISSION_STATE_PY.parent.parent / "lib"
if str(MISSION_LIB) not in sys.path:
    sys.path.insert(0, str(MISSION_LIB))

from scoring_provenance import reduce_review_aggregate


REVIEW_SCORE_KEYS = ("mission_achievement", "accuracy", "completeness", "usability")


def write_canonical_review_aggregate(root, reviews, *, iteration=1, name_prefix="fixture"):
    """Write a content-addressed aggregate whose claim is reducer-derived.

    Tests that model a current scoring decision must archive complete
    ``mission-review/1`` inputs, rather than an empty legacy aggregate.  Keep
    the reducer as the single definition of claim semantics.
    """
    # Fixture producers model the same iteration binding as aggregate-reviews;
    # never construct a replayable archive by carrying an old review forward.
    bound_reviews = [{**review, "iteration": iteration} if isinstance(review, dict) else review for review in reviews]
    aggregate = {
        "schema": "mission-review-aggregate/1",
        "iteration": iteration,
        "inputs": bound_reviews,
    }
    aggregate["score_claim"] = {"iteration": iteration, **reduce_review_aggregate(bound_reviews, expected_iteration=iteration)}
    content = (json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    archive = Path(root) / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / f"{name_prefix}-{digest[:16]}.json"
    path.write_bytes(content)
    scope = {"kind": "not-applicable", "reason_code": "non-git"}
    ref = {
        "kind": "review-aggregate",
        "path": str(path.relative_to(root)),
        "digest": "sha256:" + digest,
        "generation": digest[:16],
        "revision_scope": scope,
    }
    return path, ref, aggregate["score_claim"]


def canonical_review(scores, *, perspective="fixture", high_count=0):
    """Return one valid current review input for aggregate fixture setup."""
    def fixture_score(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 5 else 4.0

    normalized = {
        "mission_achievement": fixture_score(scores.get("mission_achievement", 4.0)),
        "accuracy": fixture_score(scores.get("accuracy", 4.0)),
        "completeness": fixture_score(scores.get("completeness", 4.0)),
        "usability": fixture_score(scores.get("usability", scores.get("practicality", 4.0))),
    }
    findings = [
        {"id": f"{perspective}-H-{index}", "severity": "High", "axis": "accuracy"}
        for index in range(high_count)
    ]
    review = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": 1,
        "scores": normalized,
        "findings": findings,
        "same_score_note": None,
    }
    if len(set(normalized.values())) == 1:
        review["same_score_note"] = "axis-specific fixture review"
    return review


@pytest.fixture
def canonical_core_plan():
    """Attach the policy-v1 canonical core-plan authority to an init fixture."""
    def attach(root):
        root = Path(root)
        state_file = root / ".mission-state" / "sessions" / "test.json"
        state = json.loads(state_file.read_text())
        plan = root / ".mission-state" / "plans" / "canonical-core.json"
        plan.parent.mkdir(exist_ok=True)
        payload = {"schema": "mission-plan/1", "steps": [{"id": "s1", "depends_on": []}]}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        plan.write_bytes(raw)
        binding = {"generation": 1, "source": "core", "source_id": "fixture-core", "selection_source": "automatic", "iteration": state["iteration"]}
        state["canonical_plan"] = {"path": str(plan.relative_to(root)), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(), **binding}
        state["planning_source_records"] = {"core:fixture-core": binding}
        state_file.write_text(json.dumps(state))
    return attach

# Claude Code/Codex のセッション識別 env。実運用では multi-session を自動有効化するが、
# テストは legacy 既定で動かすため隔離する (明示テストは env_extra/monkeypatch で注入)。
_SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


@pytest.fixture(autouse=True)
def _isolate_session_env(monkeypatch):
    """全テストを env 非依存にする: MISSION_* と Claude Code/Codex session env を除去。
    in-process (importlib) テストにも効く。subprocess は run_cli が別途遮断。"""
    for k in ("MISSION_MULTI_SESSION", "MISSION_SESSION_ID", "MISSION_SEARCH_ROOTS", *_SESSION_ENV_VARS):
        monkeypatch.delenv(k, raising=False)


def _read_state(sd):
    return json.loads((sd / "sessions" / "test.json").read_text())


@pytest.fixture
def read_state():
    return _read_state


@pytest.fixture
def state_dir(tmp_path):
    """tmp_path に .mission-state/state.json を初期化して返す."""
    sd = tmp_path / ".mission-state"
    (sd / "sessions").mkdir(parents=True)
    initial = {
        "mission": "test mission",
        "mission_id": "abc12345",
        "subtasks": [],
        "complexity": "Standard",
        "reviewer_count": 2,
        "max_iter": 5,
        "threshold": 4.0,
        "iteration": 1,
        "phase": "scoring",
        "score_history": [],
        "stagnation_count": 0,
        "decisions": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-05-25T00:00:00Z",
        "updated_at": "2026-05-25T00:00:00Z",
        "schema_version": 2,
        "project_root": str(tmp_path),
        "pid": 0,
        "hostname": "test",
        "session_id": "test",
        "created_at_session": "2026-05-25T00:00:00Z",
    }
    (sd / "sessions" / "test.json").write_text(json.dumps(initial, indent=2))
    return sd


@pytest.fixture
def run_cli(tmp_path):
    """mission-state.py をサブプロセスで呼ぶ helper.

    env isolation (2026-06-10): 既定で MISSION_* prefix の変数を
    継承環境から除去し、env_extra による明示注入のみ許す。外部セッションの
    MISSION_* 汚染でテスト結果が変わる非決定性を遮断する。
    """
    def _run(*args, cwd=None, check=False, env_extra=None):
        # MISSION_* prefix 一括遮断 (将来 mission-state.py が新しい MISSION_* を読んでも自動でマスク)
        base_env = {k: v for k, v in os.environ.items()
                    if not k.startswith("MISSION_") and k not in _SESSION_ENV_VARS}
        # env_extra でセッション識別 (MISSION_SESSION_ID/Claude Code/Codex) が明示されていなければ
        # デフォルト sid="test" を注入 (テストを sessions/test.json に固定)
        _sid_keys = ("MISSION_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
        if not (env_extra and any(k in env_extra for k in _sid_keys)):
            base_env["MISSION_SESSION_ID"] = "test"
        # Model a real caller that pre-issues and carries its fencing token. This
        # fixed token is never learned from state; explicit None tests missing-token paths.
        base_env["MISSION_LEASE_ID"] = "test-lease"
        if env_extra is not None:
            for key, value in env_extra.items():
                if value is None:
                    base_env.pop(key, None)
                else:
                    base_env[key] = value
        command_cwd = Path(cwd or tmp_path).resolve()
        command_args = list(args)
        # Successful legacy setup calls are normalized into the same
        # content-bound evidence contract used by production. Raw CLI tests
        # keep their original input because they do not request check=True.
        if check and command_args and command_args[0] == "push-score" and "--scoring-json" not in command_args:
            def option(name, default=None):
                try:
                    return command_args[command_args.index(name) + 1]
                except (ValueError, IndexError):
                    return default
            composite, minimum = float(option("--composite", "4.5")), float(option("--min-item", "4.5"))
            other = (4 * composite - minimum) / 3
            items = {
                "mission_achievement": minimum,
                "accuracy": other,
                "completeness": other,
                "usability": other,
            }
            if all(0 <= value <= 5 for value in items.values()):
                open_high = int(option("--open-high", "0"))
                _, ref, claim = write_canonical_review_aggregate(
                    command_cwd,
                    [canonical_review(items, high_count=open_high)],
                    iteration=int(option("--iteration", "1")),
                    name_prefix="legacy-normalized",
                )
                archive = command_cwd / ".mission-state" / "archive"
                score = archive / f"legacy-normalized-score-{option('--iteration', '1')}.json"
                score.write_text(json.dumps({
                    "items": claim["items"], "open_high": claim["open_high"],
                    "review_agreement": claim["review_agreement"],
                    "agreement_detail": claim["agreement_detail"],
                    "findings_evidence_path": ref["path"],
                    "score_provenance": {"score_source": "scoring-json", "review_evidence_ref": ref,
                                         "revision_scope": ref["revision_scope"]},
                }))
                cleaned = []
                skip = False
                for value in command_args:
                    if skip:
                        skip = False
                    elif value in {"--items", "--composite", "--min-item", "--scoring-output"}:
                        skip = True
                    else:
                        cleaned.append(value)
                command_args = cleaned + ["--scoring-json", str(score)]
        return subprocess.run(
            [sys.executable, str(MISSION_STATE_PY), *command_args],
            cwd=str(command_cwd),
            capture_output=True,
            text=True,
            check=check,
            env=base_env,
        )
    return _run


@pytest.fixture
def prepare_approved_invocation(run_cli):
    """Prepare and host-approve a command provider for canonical invocation tests."""
    def _prepare(*, cwd, provider, iteration, phase, env_extra=None, registry=None,
                 input_file=None, json_output=False):
        root = Path(cwd)
        provider_root = root / ".test-provider-preflight"
        provider_root.mkdir(exist_ok=True)
        source = provider_root / "test_approval_provider.py"
        source.write_text(
            "import hashlib\n"
            "def verify(request):\n"
            " nonce=hashlib.sha256(request['preflight_id'].encode()).hexdigest()[:32]\n"
            " return {**request,'schema':'approval-evidence/1','issuer_id':'test-host-event',"
            "'verifier_id':'test-verifier','verifier_version':'1.0','actor_kind':'human',"
            "'actor_id':'actor:test','proof_kind':'opaque-host-event',"
            "'proof_digest':'sha256:'+'f'*64,'expires_at':'2099-01-01T00:00:00Z',"
            "'single_use_nonce':nonce}\n",
            encoding="utf-8",
        )
        dist = provider_root / "test_approval_provider-1.0.dist-info"
        dist.mkdir(exist_ok=True)
        (dist / "METADATA").write_text(
            "Name: test-approval-provider\nVersion: 1.0\n", encoding="utf-8"
        )
        (dist / "entry_points.txt").write_text(
            "[mission.approval_verifiers]\ntest-entry = test_approval_provider:verify\n",
            encoding="utf-8",
        )
        config = root / ".test-host-config" / "mission"
        config.mkdir(parents=True, exist_ok=True)
        (config / "approval-verifiers.json").write_text(json.dumps({
            "schema": "mission-approval-verifier-registry/2",
            "verifiers": [{
                "id": "test-verifier", "entry_point": "test-entry",
                "distribution": "test-approval-provider", "version": "1.0",
                "source_digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            }],
        }), encoding="utf-8")
        env = dict(env_extra or {})
        inherited = env.get("PYTHONPATH") or os.environ.get("PYTHONPATH")
        env["PYTHONPATH"] = str(provider_root) + (os.pathsep + inherited if inherited else "")
        env["XDG_CONFIG_HOME"] = str(config.parent)
        common = ["--provider", provider, "--iteration", str(iteration), "--phase", phase]
        if input_file is None:
            input_file = provider_root / "input.txt"
            input_file.write_text("test provider input\n", encoding="utf-8")
        bound = []
        if registry is not None:
            bound += ["--registry", str(registry)]
        bound += ["--input-file", str(input_file)]
        prepared_result = run_cli(
            "specialists", "prepare-invocation", *common, *bound,
            cwd=root, env_extra=env,
        )
        assert prepared_result.returncode == 0, prepared_result.stderr
        prepared = json.loads(prepared_result.stdout)
        if prepared["requires_approval"]:
            run_cli(
                "specialists", "verify-approval", "--preflight-id", prepared["preflight_id"],
                "--evidence-ref", "sha256:" + "e" * 64,
                "--approval-verifier", "test-verifier", cwd=root, env_extra=env, check=True,
            )
        args = ["specialists", "invoke-prepared", *common,
                "--preflight-id", prepared["preflight_id"], *bound]
        if json_output:
            args.append("--json")
        return args, env, prepared
    return _prepare


@pytest.fixture
def push_provenance_score(run_cli):
    """Create the v4 typed scoring/evidence contract for tests that need a pass."""
    def _push(cwd, *, env_extra=None, iteration=1, items=None, open_high=0, notes=None):
        root = Path(cwd)
        sid = (env_extra or {}).get("MISSION_SESSION_ID", "test")
        if "CLAUDE_CODE_SESSION_ID" in (env_extra or {}) and "MISSION_SESSION_ID" not in (env_extra or {}):
            sid = "cc-" + env_extra["CLAUDE_CODE_SESSION_ID"]
        state = json.loads((root / ".mission-state" / "sessions" / f"{sid}.json").read_text())
        values = items or {"mission_achievement": 4.5, "accuracy": 4.5, "completeness": 4.5, "usability": 4.5}
        _, ref, claim = write_canonical_review_aggregate(
            root,
            [canonical_review(values, high_count=open_high)],
            iteration=iteration,
            name_prefix=f"fixture-{sid}",
        )
        archive = root / ".mission-state" / "archive"
        scoring = archive / f"fixture-score-{sid}.json"
        payload = {
            "items": claim["items"], "open_high": claim["open_high"],
            "review_agreement": claim["review_agreement"], "agreement_detail": claim["agreement_detail"],
            "findings_evidence_path": ref["path"],
            "score_provenance": {"score_source": "scoring-json", "review_evidence_ref": ref,
                                 "revision_scope": ref["revision_scope"]},
        }
        if notes is not None:
            payload["notes"] = notes
        scoring.write_text(json.dumps(payload))
        return run_cli("push-score", "--iteration", str(iteration), "--scoring-json", str(scoring), cwd=root, env_extra=env_extra, check=True)
    return _push
