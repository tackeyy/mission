"""iter2 リファクタ: 共通 search_roots ヘルパー + 抽出関数の直接単体テスト (2026-06-13)."""
import importlib.util
import json
from pathlib import Path

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("gs_iter2", MISSION_STATE_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ===== High#1: stats が home 全体でなく workspace/dev 限定 (共通ヘルパー) =====

def test_default_search_roots_env_and_cwd_default(tmp_path, monkeypatch):
    """MISSION_SEARCH_ROOTS 未設定なら cwd のみ。設定時は pathsep 区切りで ~ 展開して返す (OSS 可搬性)."""
    import os
    m = _load()
    monkeypatch.delenv("MISSION_SEARCH_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)
    assert m._default_search_roots() == [Path.cwd()]
    monkeypatch.setenv("MISSION_SEARCH_ROOTS", str(tmp_path / "a") + os.pathsep + str(tmp_path / "b"))
    assert m._default_search_roots() == [tmp_path / "a", tmp_path / "b"]


def test_stats_default_does_not_scan_home_root(tmp_path, monkeypatch):
    """デフォルト探索が Path.home() 全体を rglob しない (86秒問題の回帰防止)."""
    m = _load()
    monkeypatch.delenv("MISSION_SEARCH_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)
    roots = m._default_search_roots()
    assert Path.home() not in roots
    assert all(r != Path.home() for r in roots)


# ===== Medium: 抽出関数の直接単体テスト =====

def test_archive_scoring_output_prepends_meta(tmp_path):
    """_archive_scoring_output は meta ヘッダを前置して保存し、保存先パスを返す."""
    m = _load()
    src = tmp_path / "scorer.md"; src.write_text("本文X", encoding="utf-8")
    sd = tmp_path / ".mission-state"; sd.mkdir()
    data = {"session_id": "sid1", "agent": "claude-code", "mission_id": "deadbeef0000"}
    entry = {"timestamp": "2026-06-13T00:00:00Z"}
    dst = m._archive_scoring_output(tmp_path, str(src), 2, data, entry)
    assert dst is not None
    content = Path(dst).read_text(encoding="utf-8")
    assert "session_id=sid1" in content and "agent=claude-code" in content
    assert "本文X" in content
    assert Path(dst).name == "iter-2-deadbeef-scoring.md"


def test_archive_scoring_output_missing_file_returns_none(tmp_path, capsys):
    """ファイル不存在時は None を返し WARN を出す (後方互換)."""
    m = _load()
    (tmp_path / ".mission-state").mkdir()
    dst = m._archive_scoring_output(tmp_path, str(tmp_path / "nope.md"), 1,
                                    {"mission_id": "x"}, {"timestamp": "t"})
    assert dst is None
    err = capsys.readouterr().err
    assert "WARNING" in err and "見つかりません" in err  # 日本語化後の WARN を検証


def test_archive_scoring_output_agent_none_normalized(tmp_path):
    """agent 欠落 state では agent=unknown と表記される (stats by_agent と統一)."""
    m = _load()
    src = tmp_path / "s.md"; src.write_text("x", encoding="utf-8")
    (tmp_path / ".mission-state").mkdir()
    dst = m._archive_scoring_output(tmp_path, str(src), 1,
                                    {"mission_id": "abcdef12", "session_id": "s"}, {"timestamp": "t"})
    assert "agent=unknown" in Path(dst).read_text(encoding="utf-8")


def test_build_agent_summary_empty():
    """空リストは空 dict."""
    m = _load()
    assert m._build_agent_summary([]) == {}


def test_build_agent_summary_counts_by_agent_and_class():
    """agent 別に total/pass/halt/incomplete を集計."""
    m = _load()
    states = [
        {"agent": "claude-code", "passes": True},
        {"agent": "claude-code", "passes": False, "halt_reason": "x"},
        {"agent": "codex", "passes": False, "loop_active": True},
        {"passes": True},  # agent 欠落 → unknown
    ]
    bs = m._build_agent_summary(states)
    assert bs["claude-code"]["total"] == 2
    assert bs["claude-code"]["pass"] == 1
    assert bs["claude-code"]["halt"] == 1
    assert bs["codex"]["incomplete"] == 1
    assert bs["unknown"]["pass"] == 1


# ===== _iter_state_files include_archive の両パス =====

def _mk(sd: Path, rel: str, sid: str):
    p = sd / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"session_id": sid}), encoding="utf-8")
    return p


def test_iter_state_files_excludes_archive_by_default(tmp_path):
    """デフォルト(include_archive=False)は archive/state-*.json を含まない."""
    m = _load()
    gs = tmp_path / ".mission-state"
    _mk(gs, "sessions/a.json", "a")
    _mk(gs, "archive/state-x.json", "arch")
    found = {p.name for p in m._iter_state_files(tmp_path)}
    assert "a.json" in found
    assert "state-x.json" not in found


def test_iter_state_files_includes_archive_when_requested(tmp_path):
    """include_archive=True で archive/state-*.json を含む."""
    m = _load()
    gs = tmp_path / ".mission-state"
    _mk(gs, "sessions/a.json", "a")
    _mk(gs, "archive/state-x.json", "arch")
    found = {p.name for p in m._iter_state_files(tmp_path, include_archive=True)}
    assert "a.json" in found
    assert "state-x.json" in found


# ===== High#1 速度: 巨大ツリーのプルーニング (rglob 全舐め回避) =====

def test_iter_state_files_prunes_node_modules(tmp_path):
    """node_modules/.git 等の内側にある .mission-state はスキャンしない (速度最適化)。

    実運用で mission state が node_modules 内に作られることはなく、~/dev の 12 万
    ディレクトリ全舐め (実測 20 秒) を避けるためのプルーニング。
    """
    m = _load()
    gs = tmp_path / "proj" / ".mission-state"
    _mk(gs, "sessions/a.json", "a")
    for junk in ("node_modules", ".git", "__pycache__", "target", ".gradle", "Pods"):
        _mk(tmp_path / "proj" / junk / "pkg" / ".mission-state", "sessions/b.json", "b")
    found = {p.name for p in m._iter_state_files(tmp_path)}
    assert "a.json" in found
    assert "b.json" not in found  # 巨大ツリー内はプルーニングされる


def test_iter_state_files_finds_nested_project_state(tmp_path):
    """通常のネストしたプロジェクト (worktree 等) の .mission-state は引き続き見つかる."""
    m = _load()
    _mk(tmp_path / "a" / ".mission-state", "state.json", "x")
    _mk(tmp_path / "a" / "sub" / "b" / ".mission-state", "sessions/c.json", "y")
    found = {p.name for p in m._iter_state_files(tmp_path)}
    assert "state.json" in found
    assert "c.json" in found


# ===== iter3: 残課題対応 (2026-06-13) =====

def test_build_agent_summary_accepts_precomputed_classes():
    """classes を渡すと _classify 再計算を避ける (2N→N)。結果は再計算時と同一."""
    m = _load()
    states = [{"agent": "cli", "passes": True}, {"agent": "cli", "halt_reason": "x"}]
    classes = [m._classify(s) for s in states]
    with_classes = m._build_agent_summary(states, classes)
    without = m._build_agent_summary(states)
    assert with_classes == without
    assert with_classes["cli"]["total"] == 2
    assert with_classes["cli"]["pass"] == 1
    assert with_classes["cli"]["halt"] == 1


def test_iter_state_files_does_not_follow_symlinks(tmp_path):
    """os.walk(followlinks=False) によりシンボリックリンク先の .mission-state はたどらない。

    rglob も symlink ディレクトリを再帰展開しないため挙動は等価 (回帰防止の明示テスト)。
    """
    import os
    real = tmp_path / "real_proj"
    _mk(real / ".mission-state", "sessions/r.json", "r")
    link = tmp_path / "scan_root" / "linked"
    link.parent.mkdir(parents=True)
    os.symlink(real, link)
    found = {p.name for p in m_load_iter(tmp_path / "scan_root")}
    assert "r.json" not in found  # symlink 先は走査しない


def m_load_iter(root):
    m = _load()
    return list(m._iter_state_files(root))
