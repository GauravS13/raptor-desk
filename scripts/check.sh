#!/bin/sh
# Pre-commit gate: every check must pass before a commit is made.
set -eu

uv run ruff check .
uv run ruff format --check .
PYTHONPATH=src uv run lint-imports
uv run python src/manage.py makemigrations --check --dry-run
uv run pytest -q
