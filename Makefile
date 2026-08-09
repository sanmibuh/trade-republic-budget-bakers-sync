VENV = .venv
PYTHON = $(VENV)/bin/python

.PHONY: help test lint clean run-sync run-backup run-bot

# ── Default ─────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Dev targets (local, no Docker):"
	@echo "  test        Run the test suite with coverage"
	@echo "  lint        Run ruff linter"
	@echo "  run-sync    One-shot sync (requires env vars set)"
	@echo "  run-backup  One-shot backup auto (requires env vars set)"
	@echo "  run-bot     Start the Telegram bot (requires env vars set)"
	@echo "  clean       Remove __pycache__ and .pytest_cache"
	@echo ""
	@echo "Env vars must be set before running sync/backup/bot targets."
	@echo "Example: export \$$(cat .env | xargs) && make run-sync"
	@echo ""

# ── Dev ──────────────────────────────────────────────────────────────────────

test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .

run-sync:
	$(PYTHON) -m app sync

run-backup:
	$(PYTHON) -m app backup auto

run-bot:
	$(PYTHON) -m app bot

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage coverage.json
