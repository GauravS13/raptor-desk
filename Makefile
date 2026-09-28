# Convenience targets. Every command also works without make (see README).
.PHONY: up down reset test lint format accept check backup restore doctor

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
	python3 tools/run_extended.py .dogfood-extended.toml > acceptance-extended-report.txt
	cat acceptance-extended-report.txt
	python3 tools/truth_table.py

check:
	sh scripts/check.sh

# Backups land in the data volume under /data/backups. Copy one off the machine with:
#   docker compose cp app:/data/backups ./backups
backup:
	docker compose exec app python src/manage.py backup --keep 14

# make restore FILE=raptor-desk-YYYYMMDD-HHMMSS.tar.gz
restore:
	@test -n "$(FILE)" || (echo "usage: make restore FILE=<backup file name>" && exit 2)
	docker compose stop app worker
	docker compose run --rm --no-deps --entrypoint python app src/manage.py restore "$(FILE)" --yes
	docker compose up -d

# Health report: what is fine, what needs attention, and what to do.
doctor:
	docker compose exec app python src/manage.py doctor
