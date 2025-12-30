.PHONY: up down test lint smoke eval eval-verified eval-verified-conflicts eval-multisource eval-openai-smoke eval-openai-verified-smoke eval-evidence-integrity install-dev check

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

up:
	docker compose up --build

down:
	docker compose down -v

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy .

install-dev:
	$(PYTHON) -m pip install -U pip
	$(PYTHON) -m pip install -e ".[dev]"

check:
	$(MAKE) lint
	$(MAKE) test

smoke:
	AI_PROVIDER=fake DEBUG=true $(PYTHON) scripts/smoke/run_smoke.py

eval:
	$(PYTHON) scripts/eval/run_eval.py

eval-verified:
	$(PYTHON) scripts/eval/run_eval_verified.py

eval-verified-conflicts:
	$(PYTHON) scripts/eval/run_eval_verified.py --dataset scripts/eval/golden_verified_conflicts.json

eval-multisource:
	$(PYTHON) scripts/eval/run_eval_multisource.py

eval-openai-smoke:
	AI_PROVIDER=openai DEBUG=true $(PYTHON) tests/eval/run_eval_openai_smoke.py

eval-openai-verified-smoke:
	AI_PROVIDER=openai DEBUG=true $(PYTHON) tests/eval/run_eval_openai_verified_smoke.py

eval-evidence-integrity:
	AI_PROVIDER=openai DEBUG=true $(PYTHON) tests/eval/run_eval_evidence_integrity.py
