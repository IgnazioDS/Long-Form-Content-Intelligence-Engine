.PHONY: up down test lint

up:
	docker compose up --build

down:
	docker compose down -v

test:
	pytest

lint:
	ruff check .
	mypy .
