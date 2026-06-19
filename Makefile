.PHONY: install dev-single dev-multi up down migrate-shared migrate-tenant-schema test doctor lint format

install:
	pip install -e ".[dev]"

dev-single:
	uvicorn examples.basic_single.app:app --reload

dev-multi:
	uvicorn examples.basic_multi.app:app --reload --host 0.0.0.0

up:
	docker compose up -d

down:
	docker compose down

migrate-shared:
	alembic -c alembic_shared.ini upgrade head

migrate-tenant-schema:
	alembic -c alembic_tenant.ini upgrade head

test:
	pytest tests/ -q

doctor:
	asterion doctor

lint:
	python -m compileall -q asterion examples tests

format:
	@echo "No formatter configured. Add ruff/black to dev deps and wire here."
