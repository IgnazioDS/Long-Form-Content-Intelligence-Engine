.PHONY: up down build-prod up-prod down-prod logs-prod migrate-prod smoke-prod ci-build-prod test lint smoke eval eval-verified eval-verified-conflicts eval-multisource eval-openai-smoke eval-openai-verified-smoke eval-openai-verified-contradictions-smoke eval-evidence-integrity install-dev check

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

up:
	docker compose up --build

down:
	docker compose down -v

build-prod:
	docker compose -f docker-compose.prod.yml build

up-prod:
	docker compose -f docker-compose.prod.yml up -d --build

down-prod:
	docker compose -f docker-compose.prod.yml down -v

logs-prod:
	docker compose -f docker-compose.prod.yml logs -f

migrate-prod:
	docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head

smoke-prod:
	@set -e; \
	trap 'docker compose -f docker-compose.prod.yml down -v' EXIT; \
	AI_PROVIDER=fake DEBUG=false REQUIRE_API_KEY=false RATE_LIMIT_BACKEND=external docker compose -f docker-compose.prod.yml up -d --build; \
	echo "Waiting for http://localhost:8000/health"; \
	for i in $$(seq 1 60); do \
		if curl -fsS http://localhost:8000/health >/dev/null; then \
			break; \
		fi; \
		sleep 2; \
		if [ $$i -eq 60 ]; then \
			echo "API failed to become healthy"; \
			exit 1; \
		fi; \
	done; \
	curl -fsS http://localhost:8000/health >/dev/null; \
	curl -fsS http://localhost:8000/openapi.json >/dev/null

ci-build-prod:
	docker build -f Dockerfile.prod .

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

eval-openai-verified-contradictions-smoke:
	AI_PROVIDER=openai DEBUG=true $(PYTHON) tests/eval/run_eval_openai_verified_smoke.py --dataset tests/eval/golden_openai_verified_contradictions_smoke.json

eval-evidence-integrity:
	AI_PROVIDER=openai DEBUG=true $(PYTHON) tests/eval/run_eval_evidence_integrity.py
