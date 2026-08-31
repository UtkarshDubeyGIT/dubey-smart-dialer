.PHONY: setup up down logs test unit integration demo simulate load-test smoke verify clean

UV_CACHE_DIR ?= /tmp/credresolve-uv-cache

setup:
	@echo "[INFO] Installing the locked Python 3.12 environment..."
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --frozen
	@echo "[OK] Development environment ready."

up:
	@echo "[INFO] Building and starting PostgreSQL, API, and worker..."
	docker compose up --build -d
	@echo "[OK] SmartDialer started: http://localhost:8000/docs"

down:
	@echo "[INFO] Stopping SmartDialer services..."
	docker compose down
	@echo "[OK] SmartDialer stopped."

logs:
	docker compose logs -f api worker

unit:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest tests/unit

integration:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest tests/integration

test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

simulate:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run smart-dialer simulate --seed 2026 --output reports/simulation.json

demo: up
	@echo "[INFO] Running the prepared reviewer demo..."
	docker compose exec api smart-dialer seed-demo

load-test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m smart_dialer.load_test --output reports/load-test.json

smoke:
	@sh scripts/compose-smoke.sh

verify: test simulate
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m compileall -q src
	docker compose config -q
	@echo "[OK] Tests, simulation, compilation, and Compose validation passed."

clean:
	@echo "[INFO] Stopping services and removing the local SmartDialer database volume..."
	docker compose down -v
	@echo "[OK] Local SmartDialer state removed."
