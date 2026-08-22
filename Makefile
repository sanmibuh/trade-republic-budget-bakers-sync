VENV = .venv
PYTHON = $(VENV)/bin/python
LOCAL_INSTANCES = deploy/local/instances.yml

.PHONY: help test lint format clean run-sync run-backup run-backup-monthly run-backup-yearly run-bot

# ── Default ─────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Dev targets (local, no Docker):"
	@echo "  test                    Run the test suite with coverage"
	@echo "  lint                    Check lint and formatting (ruff check + format --check)"
	@echo "  format                  Auto-fix formatting and lint (ruff format + check --fix)"
	@echo "  run-sync INSTANCE=name  One-shot sync for a named instance"
	@echo "  run-backup              One-shot backup auto"
	@echo "  run-backup-monthly      One-shot backup for a specific month  (PARAM=YYYY-MM)"
	@echo "  run-backup-yearly       One-shot backup for a specific year   (PARAM=YYYY)"
	@echo "  run-bot                 Start the Telegram bot"
	@echo "  clean                   Remove __pycache__ and .pytest_cache"
	@echo ""
	@echo "Config is read from $(LOCAL_INSTANCES)."
	@echo "Copy deploy/local/instances.yml.template to $(LOCAL_INSTANCES) and fill in values."
	@echo ""
	@echo "Examples:"
	@echo "  make run-sync INSTANCE=user1"
	@echo "  make run-backup"
	@echo "  make run-backup-yearly PARAM=2025"
	@echo "  make run-backup-monthly PARAM=2026-07"
	@echo ""

# ── Helpers ──────────────────────────────────────────────────────────────────

# Fail with a helpful message if instances.yml is missing.
_check_instances:
	@test -f $(LOCAL_INSTANCES) || (echo "Missing $(LOCAL_INSTANCES) — copy deploy/local/instances.yml.template and fill in values." && exit 1)

# ── Dev ──────────────────────────────────────────────────────────────────────

test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

run-sync: _check_instances
	@test -n "$(INSTANCE)" || (echo "Usage: make run-sync INSTANCE=<name>" && exit 1)
	INSTANCES_CONFIG=$(LOCAL_INSTANCES) $(PYTHON) -m app sync --instance $(INSTANCE)

run-backup: _check_instances
	INSTANCES_CONFIG=$(LOCAL_INSTANCES) $(PYTHON) -m app backup auto

run-backup-monthly: _check_instances
	INSTANCES_CONFIG=$(LOCAL_INSTANCES) $(PYTHON) -m app backup monthly $(PARAM)

run-backup-yearly: _check_instances
	INSTANCES_CONFIG=$(LOCAL_INSTANCES) $(PYTHON) -m app backup yearly $(PARAM)

run-bot: _check_instances
	INSTANCES_CONFIG=$(LOCAL_INSTANCES) $(PYTHON) -m app bot

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage coverage.json
