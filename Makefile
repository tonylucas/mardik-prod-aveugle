.PHONY: up down test trace fmt lint typecheck install

install:
	uv sync

up:
	docker compose up -d

down:
	docker compose down

test:
	uv run pytest -v

trace:
	uv run python scripts/emit_traces.py

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src
