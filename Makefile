.PHONY: install dev up down migrate-shared migrate-tenant-schema test

install:
	pip install -e ".[dev]"

dev:
	uvicorn coreAdmin_api.main:app --reload

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
