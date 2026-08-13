VENV = .venv
PYTHON = $(VENV)/bin/python
LOCAL_ENV = deploy/local/local.env

.PHONY: help test lint format clean run-sync run-backup run-backup-monthly run-backup-yearly run-bot

# ── Default ─────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Dev targets (local, no Docker):"
	@echo "  test                    Run the test suite with coverage"
	@echo "  lint                    Check lint and formatting (ruff check + format --check)"
	@echo "  format                  Auto-fix formatting (ruff format)"
	@echo "  run-sync                One-shot sync"
	@echo "  run-backup              One-shot backup auto"
	@echo "  run-backup-monthly      One-shot backup for a specific month  (PARAM=YYYY-MM)"
	@echo "  run-backup-yearly       One-shot backup for a specific year   (PARAM=YYYY)"
	@echo "  run-bot                 Start the Telegram bot"
	@echo "  clean                   Remove __pycache__ and .pytest_cache"
	@echo ""
	@echo "Env vars are loaded from $(LOCAL_ENV)."
	@echo "Copy deploy/local/local.env.template to $(LOCAL_ENV) and fill in values."
	@echo ""
	@echo "Examples:"
	@echo "  make run-backup"
	@echo "  make run-backup-yearly PARAM=2025"
	@echo "  make run-backup-monthly PARAM=2026-07"
	@echo ""

# ── Helpers ──────────────────────────────────────────────────────────────────

# Load local.env if it exists; fail with a helpful message if a target needs it.
_load_env:
	@test -f $(LOCAL_ENV) || (echo "Missing $(LOCAL_ENV) — copy deploy/local/local.env.template and fill in values." && exit 1)

# ── Dev ──────────────────────────────────────────────────────────────────────

test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

run-sync: _load_env
	@set -a && . $(LOCAL_ENV) && set +a && $(PYTHON) -m app sync

run-backup: _load_env
	@set -a && . $(LOCAL_ENV) && set +a && $(PYTHON) -m app backup auto

run-backup-monthly: _load_env
	@set -a && . $(LOCAL_ENV) && set +a && $(PYTHON) -m app backup monthly $(PARAM)

run-backup-yearly: _load_env
	@set -a && . $(LOCAL_ENV) && set +a && $(PYTHON) -m app backup yearly $(PARAM)

run-bot: _load_env
	@set -a && . $(LOCAL_ENV) && set +a && $(PYTHON) -m app bot

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage coverage.json
