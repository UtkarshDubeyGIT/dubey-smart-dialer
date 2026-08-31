.PHONY: setup up down logs test unit integration demo simulate load-test verify clean

UV_CACHE_DIR ?= /tmp/credresolve-uv-cache

setup:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --frozen

up:
	docker compose up --build -d
	@echo "SmartDialer: http://localhost:8000/docs"

down:
	docker compose down

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
	docker compose exec api smart-dialer seed-demo

load-test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m smart_dialer.load_test --output reports/load-test.json

verify: test simulate
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m compileall -q src
	docker compose config -q

clean:
	docker compose down -v
