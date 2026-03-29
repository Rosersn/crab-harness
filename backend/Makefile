install:
	uv sync

dev:
	PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload --reload-include='*.yaml'

gateway:
	PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

infra:
	docker compose -f docker-compose.dev.yml up -d

infra-down:
	docker compose -f docker-compose.dev.yml down

test:
	PYTHONPATH=. uv run pytest tests/ -v

lint:
	uvx ruff check .

format:
	uvx ruff check . --fix && uvx ruff format .
