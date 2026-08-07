COMPOSE_FILE   ?= docker-compose.yml
SERVICE        ?= tr-sync-david
BASE_IMAGE     ?= tr-sync-base
IMAGE          ?= tr-wallet-sync
DATA_DIR       ?= ./data/david
OUTPUT_DIR     ?= ./output/david

.PHONY: help build-base build build-all bootstrap sync \
        docker-build docker-rebuild docker-bootstrap docker-sync \
        test clean

# ── Default ────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Usage: make <target> [VARIABLE=value]"
	@echo ""
	@echo "Docker Compose targets  (uses COMPOSE_FILE=$(COMPOSE_FILE), SERVICE=$(SERVICE))"
	@echo "  build-base      Build the base image (python + git + pip install) -- run when requirements.txt or Python version changes"
	@echo "  build           Build the app image only (assumes base exists) -- run after code changes"
	@echo "  build-all       Rebuild base + app both from scratch (no cache) -- full clean build"
	@echo "  bootstrap       Run interactively to complete first-time 2FA login"
	@echo "  sync            Run a one-shot sync"
	@echo ""
	@echo "Plain Docker targets    (uses IMAGE=$(IMAGE))"
	@echo "  docker-build    Build the image with docker build (uses cache)"
	@echo "  docker-rebuild  Build the image with docker build (no cache)"
	@echo "  docker-bootstrap  Run interactively to complete first-time 2FA login"
	@echo "  docker-sync     Run a one-shot sync"
	@echo ""
	@echo "Dev targets"
	@echo "  test            Run the test suite"
	@echo "  clean           Remove __pycache__ and .pytest_cache"
	@echo ""

# ── Docker Compose ──────────────────────────────────────────────────────────

build-base:
	docker build -f Dockerfile.base -t $(BASE_IMAGE):latest .

build:
	docker compose -f $(COMPOSE_FILE) build $(SERVICE)

build-all:
	docker build --no-cache -f Dockerfile.base -t $(BASE_IMAGE):latest .
	docker compose -f $(COMPOSE_FILE) build --no-cache $(SERVICE)

bootstrap:
	@mkdir -p $(DATA_DIR) $(OUTPUT_DIR)
	docker compose -f $(COMPOSE_FILE) run --rm -it $(SERVICE)

sync:
	@mkdir -p $(DATA_DIR) $(OUTPUT_DIR)
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE)

# ── Plain Docker ────────────────────────────────────────────────────────────

docker-build:
	docker build -t $(IMAGE) .

docker-rebuild:
	docker build --no-cache -t $(IMAGE) .

docker-bootstrap:
	@mkdir -p $(DATA_DIR) $(OUTPUT_DIR)
	docker run --rm -it \
		--env-file .env \
		-v "$(PWD)/$(DATA_DIR):/app/data" \
		-v "$(PWD)/$(OUTPUT_DIR):/app/output" \
		$(IMAGE)

docker-sync:
	@mkdir -p $(DATA_DIR) $(OUTPUT_DIR)
	docker run --rm \
		--env-file .env \
		-v "$(PWD)/$(DATA_DIR):/app/data" \
		-v "$(PWD)/$(OUTPUT_DIR):/app/output" \
		$(IMAGE)

# ── Dev ─────────────────────────────────────────────────────────────────────

test:
	.venv/bin/pytest tests/ -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
