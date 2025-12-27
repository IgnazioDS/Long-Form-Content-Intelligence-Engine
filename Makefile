.PHONY: up down test lint smoke eval

up:
	docker compose up --build

down:
	docker compose down -v

test:
	pytest

lint:
	ruff check .
	mypy .

smoke:
	AI_PROVIDER=fake DEBUG=true python3 scripts/smoke/run_smoke.py

eval:
	python3 scripts/eval/run_eval.py
