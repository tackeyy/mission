PYTHON ?= python3
VENV ?= .venv-ci
VENV_PYTHON := $(VENV)/bin/python
REQUIREMENTS := .github/requirements-ci.txt
REQUIREMENTS_STAMP := $(VENV)/.requirements-ci.stamp

.PHONY: test-smoke test test-e2e

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
	$(VENV_PYTHON) -m pytest -q skills/mission
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"full","test_manifest":["skills/mission"]}\n' "$$(git rev-parse 'HEAD^{tree}')"

test-e2e: $(REQUIREMENTS_STAMP)
	$(VENV_PYTHON) -m pytest -q skills/mission -k 'e2e or operational'
	@printf '{"schema":"mission-test-report/1","tree_sha":"%s","tier":"e2e","test_manifest":["skills/mission","-k","e2e or operational"]}\n' "$$(git rev-parse 'HEAD^{tree}')"
