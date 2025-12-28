.PHONY: up down test lint smoke eval eval-verified install-dev check

up:
	docker compose up --build

down:
	docker compose down -v

test:
	pytest

lint:
	ruff check .
	mypy .

install-dev:
	python3 -m pip install -U pip
	python3 -m pip install -e ".[dev]"

check:
	$(MAKE) lint
	$(MAKE) test

smoke:
	AI_PROVIDER=fake DEBUG=true python3 scripts/smoke/run_smoke.py

eval:
	python3 scripts/eval/run_eval.py

eval-verified:
	python3 scripts/eval/run_eval_verified.py
