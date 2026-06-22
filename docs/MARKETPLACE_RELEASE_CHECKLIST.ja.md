# Marketplace Release Checklist

Claude Code / Codex marketplace に `mission` を出す前に使うチェックリスト。

開始前に、これは通常の merge release ではなく **distribution release** であることを確認します。詳細は [`VERSIONING.ja.md`](VERSIONING.ja.md) を参照してください。PR を merge するたびに version を上げません。

## 共通

- [ ] この release は新しい plugin version を意図的に配布するものである。通常の PR merge では version を変えず、entry は `[Unreleased]` に残す。
- [ ] repository visibility の変更は意図的なもの。通常検証の一部として public 化しない。
- [ ] `git status --short` に意図した release 変更だけが残っている。
- [ ] secret、token、private URL、local-only state、`.mission-state/`、`.pytest_cache/`、個人マシン固有の絶対パスが含まれていない。
- [ ] `LICENSE`、`README.md`、`SECURITY.md`、`CONTRIBUTING.md`、Code of Conduct が揃っている。
- [ ] user-facing な install / release 手順を変えた場合、英日 docs を同時に更新している。
- [ ] `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`plugins/mission/.codex-plugin/plugin.json`、README の install path、Codex setup docs の version number を同時に bump している。
- [ ] `CHANGELOG.md` と `CHANGELOG.ja.md` に新 version の release entry と link を追加している。
- [ ] `git log <previous-tag>..HEAD --oneline` と新しい changelog entry を突合し、user-facing な `feat:` / `fix:` / audit / release-process 変更が英日両方に反映されている。
- [ ] release 完了報告前に、新しい `vX.Y.Z` tag の GitHub Releases を作成または更新している。
- [ ] `skills/mission` で `python3 -m pytest -q` が pass する。
- [ ] `shellcheck scripts/mission-stop-guard.sh` が pass する。
- [ ] JSON / YAML metadata が parse できる。

## Claude Code

- [ ] `claude plugin validate /path/to/mission` が warning なしで pass する。
- [ ] isolated install が成功する。
  - `CLAUDE_CONFIG_DIR="$(mktemp -d)"`
  - `claude plugin marketplace add /path/to/mission`
  - `claude plugin install mission@mission-marketplace`
- [ ] `claude plugin details mission` に 6 skills と 1 `Stop` hook が表示される。
- [ ] active mission state がある実 Claude Code session で、`Stop` hook の `decision:block` response が出る。

## Codex

- [ ] `skills/` または `scripts/` を変えた後、`scripts/sync-codex-plugin-wrapper.sh` を実行している。
- [ ] isolated `CODEX_HOME` で `codex plugin marketplace add /path/to/mission` が成功する。
- [ ] `codex plugin list` に `mission@mission-marketplace` が表示される。
- [ ] `codex plugin add mission@mission-marketplace` が成功する。
- [ ] install cache に `.codex-plugin/plugin.json`、`skills/mission/SKILL.md`、`skills/mission/bin/mission-state.py`、`scripts/mission-stop-guard.sh` が入っている。
- [ ] install cache に `.git`、`.mission-state`、`.pytest_cache` が入っていない。
- [ ] `MISSION_PLUGIN_ROOT` と互換 alias `CLAUDE_PLUGIN_ROOT` を install cache path 用に案内している。
- [ ] Codex Stop hook は、Codex の plugin-root path handling が検証できるまでは opt-in user hook として記載している。

## Final Gate

- [ ] GitHub repository visibility が意図した値のままであることを確認する。
- [ ] applicable checks がすべて pass してから commit / push する。
