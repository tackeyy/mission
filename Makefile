PYTHON ?= python3
VENV ?= .venv-ci
VENV_PYTHON := $(VENV)/bin/python
REQUIREMENTS := .github/requirements-ci.txt
REQUIREMENTS_STAMP := $(VENV)/.requirements-ci.stamp
PYTEST_TARGETS ?= skills/mission
SHARD_INDEX ?= 1
SHARD_TOTAL ?= 1

.PHONY: test-smoke test test-shard test-e2e

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(REQUIREMENTS_STAMP): $(REQUIREMENTS) | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install -r $(REQUIREMENTS)
	@touch $(REQUIREMENTS_STAMP)

test-smoke:
	$(PYTHON) -m py_compile skills/mission/bin/mission-state.py scripts/mission-audit.py
	bash -n scripts/mission-stop-guard.sh
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"smoke","test_manifest":["skills/mission/bin/mission-state.py","scripts/mission-audit.py","scripts/mission-stop-guard.sh"]}\n' "$$(git rev-parse 'HEAD^{tree}')"

test: $(REQUIREMENTS_STAMP)
	$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile $(PYTEST_TARGETS)
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"full","test_manifest":["skills/mission"]}\n' "$$(git rev-parse 'HEAD^{tree}')"

# POSIX sh では「変数代入の右辺のコマンド置換が失敗した」場合に set -e が発火しない。
# 分割スクリプトが非 0 で終了しても targets="" の代入自体は成功してしまうため、
# 直後の test -n が唯一の防壁になる（これが無いと引数なし pytest へ退化する）。
# この test -n は test_actions_cost_guard.py が固定しており、除去すると CI が落ちる。
test-shard: $(REQUIREMENTS_STAMP)
	@set -eu; \
	targets="$$($(PYTHON) scripts/ci_shard_targets.py --index $(SHARD_INDEX) --total $(SHARD_TOTAL) --targets "$(PYTEST_TARGETS)")"; \
	test -n "$$targets"; \
	$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile $$targets
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"shard","shard":"%s/%s","test_manifest":["skills/mission"]}\n' "$$(git rev-parse 'HEAD^{tree}')" "$(SHARD_INDEX)" "$(SHARD_TOTAL)"

test-e2e: $(REQUIREMENTS_STAMP)
	$(VENV_PYTHON) -m pytest -q -n auto --dist loadfile skills/mission -k 'e2e or operational'
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"e2e","test_manifest":["skills/mission","-k","e2e or operational"]}\n' "$$(git rev-parse 'HEAD^{tree}')"
