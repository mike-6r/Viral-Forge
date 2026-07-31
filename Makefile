.PHONY: up down reset migrate test lint typecheck
up:
	docker compose up --build
down:
	docker compose down
reset:
	docker compose down -v
migrate:
	docker compose run --rm api alembic upgrade head
test:
	python -m pytest
lint:
	python -m ruff check .
typecheck:
	python -m mypy app
