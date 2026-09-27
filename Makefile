# Convenience targets. Every command also works without make (see README).
.PHONY: up down reset test lint format accept

up:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down -v

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	PYTHONPATH=src uv run lint-imports

format:
	uv run ruff format .
	uv run ruff check --fix .

accept:
	python3 tools/run.py .dogfood.toml --fixtures data/fixtures.json > acceptance-report.txt
	cat acceptance-report.txt
