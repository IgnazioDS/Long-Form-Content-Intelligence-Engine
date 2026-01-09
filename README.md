# Long-Form Content Intelligence Engine (MVP)

## Setup

1. Copy the environment template and fill in values:
   ```bash
   cp .env.example .env
   ```
2. Ensure Docker is running.
3. Start the stack:
   ```bash
   make up
   ```

## Production (Docker)

1. Copy the environment template and fill in values:
   ```bash
   cp .env.example .env
   ```
   For production defaults (retention + backups enabled), start from:
   ```bash
   cp .env.prod.example .env
   ```
2. Set production flags in `.env`:
   - `REQUIRE_API_KEY=true`
   - `API_KEY=...`
   - `DEBUG=false` (debug router is not mounted)
   - `RATE_LIMIT_BACKEND=external` (enforce limits at your ingress)
   - `NEXT_PUBLIC_API_BASE_URL=https://<your-domain>/api`
3. Place TLS certs in `ops/nginx/certs/`:
   - `ops/nginx/certs/fullchain.pem`
   - `ops/nginx/certs/privkey.pem`
   Provision via Let's Encrypt (certbot) or your certificate authority.
4. Build and start the production stack:
   ```bash
   make build-prod && make up-prod
   ```

Recommended: terminate TLS and enforce rate limiting at your gateway/ingress (nginx, ALB,
Cloudflare, etc). Configure metrics and tracing explicitly with `METRICS_*` and `OTEL_*`
flags when deploying to production.

Notes:
- Nginx terminates TLS and proxies `/` to the web app and `/api` to the API. The API and
  web services are not published directly in production compose.
- The production image uses a wheel-based install and requires `constraints.lock` for
  deterministic builds. Regenerate it when dependency versions change.
- Update `ops/nginx/nginx.conf` `set_real_ip_from` entries to match your ingress/LB
  networks so rate limiting uses correct client IPs.
- Run migrations as a one-off job before scaling API replicas:
  ```bash
  make migrate-prod
  ```
- Postgres/Redis ports are not published in production compose. For local debugging, add
  `ports:` entries or create an override file.
- If you change `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, update `DATABASE_URL`
  to match.

## Required Environment Variables

- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `AI_PROVIDER` (`openai` or `fake`, default: `openai`)
- `DEBUG` (default: `false`; keep `false` in production)
- `DATABASE_URL`
- `REDIS_URL`
- `MAX_CHUNKS_PER_QUERY` (default: `8`)
- `RERANK_ENABLED` (default: `true`)
- `RERANK_CANDIDATES` (default: `30`)
- `RERANK_SNIPPET_CHARS` (default: `900`)
- `CHUNK_CHAR_TARGET` (default: `5000`)
- `CHUNK_CHAR_OVERLAP` (default: `800`)

## Optional Environment Variables

- `API_KEY` (if set, send `X-API-Key` on requests; required in production if `REQUIRE_API_KEY=true`)
- `REQUIRE_API_KEY` (default: `false`; set `true` in production to require `API_KEY` at startup)
- `RATE_LIMIT_BACKEND` (`memory` or `external`, default: `memory`; use `external` in production)
- `RATE_LIMIT_RPS` (default: `0`, disabled when `0`; use only with `RATE_LIMIT_BACKEND=memory`)
- `RATE_LIMIT_BURST` (default: `0`; use only with `RATE_LIMIT_BACKEND=memory`)
- `MMR_ENABLED` (default: `true`)
- `MMR_LAMBDA` (default: `0.7`)
- `MMR_CANDIDATES` (default: `30`)
- `MAX_PDF_BYTES` (default: `25000000`)
- `MAX_PDF_PAGES` (default: `300`)
- `MAX_URL_BYTES` (default: `2000000`)
- `MAX_TEXT_BYTES` (default: `2000000`)
- `EMBED_BATCH_SIZE` (default: `64`)
- `EMBED_DIM` (default: `1536`; must match the pgvector column size)
- `OPENAI_TIMEOUT_SECONDS` (default: `30`)
- `OPENAI_MAX_RETRIES` (default: `3`)
- `POSTGRES_USER` (default: `postgres`; compose only)
- `POSTGRES_PASSWORD` (default: `postgres`; compose only)
- `POSTGRES_DB` (default: `lfcie`; compose only)
- `DB_POOL_SIZE` (default: `5`)
- `DB_MAX_OVERFLOW` (default: `10`)
- `DB_POOL_TIMEOUT` (default: `30`)
- `DB_POOL_RECYCLE` (default: `1800`)
- `DB_CONNECT_TIMEOUT` (default: `10`)
- `LOG_LEVEL` (default: `INFO`)
- `METRICS_ENABLED` (default: `true`)
- `METRICS_PATH` (default: `/metrics`)
- `OTEL_ENABLED` (default: `false`)
- `OTEL_SERVICE_NAME` (default: `long-form-content-intelligence-api`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` (default: unset; uses OpenTelemetry defaults)
- `URL_ALLOWLIST` (default: empty; comma-separated hostnames allowed for URL ingest; use `*.example.com` or `.example.com` for subdomains)
- `STORAGE_ROOT` (default: `storage`; relative paths are resolved from the repo root)
- `WORKER_CONCURRENCY` (default: `2`)
- `WORKER_PREFETCH_MULTIPLIER` (default: `1`)
- `WORKER_MAX_TASKS_PER_CHILD` (default: `100`)
- `WORKER_VISIBILITY_TIMEOUT` (default: `3600`)
- `WORKER_TASK_TIME_LIMIT` (default: `0`, disabled when `0`)
- `WORKER_TASK_SOFT_TIME_LIMIT` (default: `0`, disabled when `0`)
- `RETENTION_ENABLED` (default: `false`)
- `RETENTION_DAYS_SOURCES` (default: `0`, disabled when `0`)
- `RETENTION_DAYS_QUERIES` (default: `0`, disabled when `0`)
- `RETENTION_DAYS_ANSWERS` (default: `0`, disabled when `0`)
- `RETENTION_BATCH_SIZE` (default: `200`)
- `RETENTION_INTERVAL_SECONDS` (default: `86400`)
- `BACKUP_INTERVAL_SECONDS` (default: `86400`)
- `BACKUP_RETENTION_DAYS` (default: `7`)

Production rate limiting: set `RATE_LIMIT_BACKEND=external` and enforce limits at your
gateway/ingress (nginx, Cloudflare, ALB, etc). The in-app limiter is in-memory and
intended for dev or single-worker use only.

## Production Checklist

- `REQUIRE_API_KEY=true`
- `API_KEY` set to a non-empty value
- `DEBUG=false` (debug routes not mounted)
- `RATE_LIMIT_BACKEND=external` and gateway/ingress rate limiting configured
- `RETENTION_ENABLED=true` with retention windows set for `RETENTION_DAYS_*`
- `make migrate-prod` run before scaling API replicas
- Backups enabled via the compose `backup` profile (see below)

## Retention & Backups (Production)

Retention runs in the `maintenance` service. Enable it by setting `RETENTION_ENABLED=true`
and choose retention windows (days) for sources/queries/answers. The service runs at
`RETENTION_INTERVAL_SECONDS` and deletes old rows plus source files on disk.

Backups are provided by the optional `backup` compose profile (Postgres `pg_dump`).
Enable it with:

```bash
docker compose -f docker-compose.prod.yml --profile backup up -d
```

Backups are written to the `backups_data` volume using `BACKUP_INTERVAL_SECONDS` and
pruned after `BACKUP_RETENTION_DAYS`.

### Restore runbook (pg_dump)

1. Ensure the backup profile is running:
   ```bash
   docker compose -f docker-compose.prod.yml --profile backup up -d
   ```
2. List available dumps:
   ```bash
   docker compose -f docker-compose.prod.yml exec backup ls -1 /backups
   ```
3. Stop write-heavy services:
   ```bash
   docker compose -f docker-compose.prod.yml stop api worker maintenance
   ```
4. Restore the chosen dump (destructive, replaces current DB):
   ```bash
   docker compose -f docker-compose.prod.yml exec backup sh -c \
     'pg_restore -h postgres -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists /backups/<dump-file>.dump'
   ```
5. Start services again:
   ```bash
   docker compose -f docker-compose.prod.yml start api worker maintenance
   ```

Handy commands:
- `make retention-prod` (run retention once)
- `make retention-prod-dry-run` (preview retention deletes)
- `make backup-prod` (start the backup profile)

## How to Run Locally

```bash
make up
```

The API will be available at `http://localhost:8000`.
The UI will be available at `http://localhost:3000`.

### Local run options (exact commands)

Option A (Docker UI, 2 terminals):

Terminal 1 (stack + UI):
```bash
cd /Users/ignaziodesantis/Desktop/Development/Long-Form-Content-Intelligence-Engine
AI_PROVIDER=fake DEBUG=true REQUIRE_API_KEY=false docker compose up --build
```

Terminal 2 (smoke test):
```bash
cd /Users/ignaziodesantis/Desktop/Development/Long-Form-Content-Intelligence-Engine
source .venv/bin/activate
make smoke
```

Option B (Local UI, 3 terminals):

Terminal 1 (stack only, no UI):
```bash
cd /Users/ignaziodesantis/Desktop/Development/Long-Form-Content-Intelligence-Engine
AI_PROVIDER=fake DEBUG=true REQUIRE_API_KEY=false docker compose up --build postgres redis api worker
```

Terminal 2 (smoke test):
```bash
cd /Users/ignaziodesantis/Desktop/Development/Long-Form-Content-Intelligence-Engine
source .venv/bin/activate
make smoke
```

Terminal 3 (Next.js UI):
```bash
cd /Users/ignaziodesantis/Desktop/Development/Long-Form-Content-Intelligence-Engine/apps/web
npm install
cp .env.local.example .env.local
npm run dev
```
For production, set `REQUIRE_API_KEY=true` and define a non-empty `API_KEY`.

Debug endpoints under `/debug/*` are only mounted when `DEBUG=true` and are excluded from
OpenAPI when `DEBUG=false` (recommended for production).

## Observability

Metrics are exposed via Prometheus text format on `METRICS_PATH` when `METRICS_ENABLED=true`.
Route labels use FastAPI route templates to avoid high-cardinality raw paths.

Example:
```bash
curl http://localhost:8000/metrics
```

Tracing is disabled by default. To enable OpenTelemetry exporting:
```bash
OTEL_ENABLED=true \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces \
make up
```

## Host Setup (Required for make test/make lint)

With your venv active, run:
```bash
python3 -m pip install -U pip
python3 -m pip install -e ".[dev]"
```

`pip install -e ".[dev]"` provides `pytest`, `ruff`, and `mypy` for `make test` and `make lint`.

## Local Dev (Host)

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"

make test
make lint
```

## Example curl Commands

Health check:
```bash
curl http://localhost:8000/health
```

Dependency health:
```bash
curl http://localhost:8000/health/deps
```

Upload a PDF:
```bash
curl -F "file=@/path/to/document.pdf" -F "title=My Doc" \
  http://localhost:8000/sources/upload
```

Ingest raw text:
```bash
curl -X POST http://localhost:8000/sources/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Paste long-form text here.", "title": "My Text"}'
```

Ingest a URL:
```bash
curl -X POST http://localhost:8000/sources/ingest \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "title": "Example"}'
```

List sources:
```bash
curl http://localhost:8000/sources
```

List sources with pagination and filtering:
```bash
curl "http://localhost:8000/sources?limit=25&offset=0&status=READY&source_type=pdf"
```

Query with RAG:
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main thesis?", "source_ids": ["YOUR_SOURCE_UUID"]}'
```

Optional idempotency (replays return the same answer):
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 5f84a3a1-6e8d-4c8f-9b2f-3d1c40f5f6b1" \
  -d '{"question": "What is the main thesis?", "source_ids": ["YOUR_SOURCE_UUID"]}'
```

Query with verification:
```bash
curl -X POST http://localhost:8000/query/verified \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main thesis?", "source_ids": ["YOUR_SOURCE_UUID"]}'
```

Persisted-answer read endpoints (API key required if `API_KEY` is set):
- `GET /answers/{answer_id}`
- `GET /answers/{answer_id}/highlights`
- `GET /answers/{answer_id}/grouped`
- `GET /answers/{answer_id}/grouped/highlights`
List answers with pagination:
```bash
curl "http://localhost:8000/answers?limit=25&offset=0"
```
Filter answers by query:
```bash
curl "http://localhost:8000/answers?query_id=YOUR_QUERY_UUID"
```
Legacy-tolerant hydration:
- Missing `verification_summary`/`answer_style` is derived and normalized to satisfy strict contracts.
- Summary counts and `answer_style` are repaired to stay consistent with claims/verdicts.
- `raw_citations` may be missing or malformed (non-dict) and is treated as `{}`.
- `citations_count` uses `len(raw_citations["ids"])` if it is a list; otherwise falls back to `len(citations)`.
- Summary input selection prefers raw claims only if coerced claims are non-empty; otherwise uses raw highlights if list.
- Non-fatal consistency logging emits `verification_summary_inconsistent` when repaired payloads still mismatch.
Citations behavior:
- If `raw_citations.citations` or `raw_citations.citation_groups` are persisted, they are returned.
- If only legacy ids exist, citations may be empty; normalization still uses ids length as `citations_count`.

Delete a source:
```bash
curl -X DELETE http://localhost:8000/sources/YOUR_SOURCE_UUID
```

## Development

Run tests:
```bash
make test
```

Lint and type-check:
```bash
make lint
```

## Docker Smoke Test

```bash
AI_PROVIDER=fake DEBUG=true docker compose up --build
```

In another terminal (with the venv active):
```bash
source .venv/bin/activate
make smoke
```

## Evaluation

Recommended env: `AI_PROVIDER=fake` and `DEBUG=true` (needed for citation validation).
Eval runners also respect:
- `EVAL_READY_TIMEOUT_SECONDS` (default: 60) for source ingest readiness.
- `EVAL_HTTP_TIMEOUT_SECONDS` (default: 30) for HTTP client timeouts.

1. Start the stack:
   ```bash
   AI_PROVIDER=fake DEBUG=true docker compose up --build
   ```
2. Run the eval harness:
   ```bash
   make eval
   ```
3. Run the verified eval harness:
   ```bash
   make eval-verified
   ```
4. Run the verified conflicts eval harness:
   ```bash
   make eval-verified-conflicts
   ```
5. Run the multisource eval harness:
   ```bash
   make eval-multisource
   ```
6. Run the OpenAI highlights smoke harness:
   ```bash
   make eval-openai-smoke
   ```
   Requires `AI_PROVIDER=openai`, `DEBUG=true`, and `OPENAI_API_KEY` (exported or set in `.env`).
   Example:
   ```bash
   AI_PROVIDER=openai DEBUG=true OPENAI_API_KEY=... docker compose up --build -d
   make eval-openai-smoke
   ```
7. Run the OpenAI verified smoke harness:
   ```bash
   make eval-openai-verified-smoke
   ```
   Requires `AI_PROVIDER=openai`, `DEBUG=true`, and `OPENAI_API_KEY` (exported or set in `.env`).
   Example:
   ```bash
   AI_PROVIDER=openai DEBUG=true OPENAI_API_KEY=... docker compose up --build -d
   make eval-openai-verified-smoke
   ```
8. Run the OpenAI verified contradictions smoke harness:
   ```bash
   make eval-openai-verified-contradictions-smoke
   ```
   Requires `AI_PROVIDER=openai`, `DEBUG=true`, and `OPENAI_API_KEY` (exported or set in `.env`).
   Example:
   ```bash
   AI_PROVIDER=openai DEBUG=true OPENAI_API_KEY=... docker compose up --build -d
   make eval-openai-verified-contradictions-smoke
   ```
9. Run the evidence integrity harness:
   ```bash
   make eval-evidence-integrity
   ```
   Requires `AI_PROVIDER=openai`, `DEBUG=true`, and `OPENAI_API_KEY` (exported or set in `.env`).
   Example:
   ```bash
   AI_PROVIDER=openai DEBUG=true OPENAI_API_KEY=... docker compose up --build -d
   make eval-evidence-integrity
   ```

Thresholds live in `scripts/eval/thresholds.json`. Override them explicitly:
```bash
python3 scripts/eval/run_eval.py --thresholds scripts/eval/thresholds.json
python3 scripts/eval/run_eval_verified.py --thresholds scripts/eval/thresholds.json
```

CI enforces the quality gates in `scripts/eval/thresholds.json` for all eval runs.

The conflicts dataset uses `scripts/eval/golden_verified_conflicts.json` and fixture
`scripts/fixtures/conflicts.pdf` with profile `conflicts`. Its thresholds live under
`eval_verified_conflicts` in `scripts/eval/thresholds.json`.
Conflict thresholds are selected when the dataset profile is `conflicts` or
`eval_verified_conflicts`, when the fixture is `conflicts.pdf`, or when the dataset
filename includes `conflicts`. If a conflicts profile is set without a fixture, the
runner defaults to `conflicts.pdf`.

The multisource dataset uses `scripts/eval/golden_multisource.json` with fixtures
`scripts/fixtures/sample.pdf` and `scripts/fixtures/second.pdf`. Its thresholds live
under `eval_multisource` in `scripts/eval/thresholds.json`.

The OpenAI highlights smoke dataset uses `tests/eval/golden_openai_smoke.json` and
`scripts/fixtures/sample.pdf` to validate highlight span invariants against stored
chunk text (OpenAI spans are validated against the truncated prefix but slices are
checked against the full chunk text).

The OpenAI verified smoke dataset uses `tests/eval/golden_openai_verified_smoke.json` and
`scripts/fixtures/sample.pdf` to validate verification_summary and answer_style invariants
for `/query/verified` and `/query/verified/highlights` (no exact answer matching).

The OpenAI verified contradictions smoke dataset uses
`tests/eval/golden_openai_verified_contradictions_smoke.json` with
`scripts/fixtures/contradictions_smoke.pdf` to validate conflict rewriting and prefix
behavior for verified endpoints.

Outputs are written to:
- `scripts/eval/out/eval_results.json`
- `scripts/eval/out/eval_report.md`
- `scripts/eval/out/eval_verified_results.json`
- `scripts/eval/out/eval_verified_report.md`
- `scripts/eval/out/eval_multisource_results.json`
- `scripts/eval/out/eval_multisource_report.md`
- `tests/eval/out/eval_openai_smoke_results.json`
- `tests/eval/out/eval_openai_smoke_report.md`
- `tests/eval/out/eval_openai_verified_smoke_results.json`
- `tests/eval/out/eval_openai_verified_smoke_report.md`
- `tests/eval/out/eval_evidence_integrity_results.json`
- `tests/eval/out/eval_evidence_integrity_report.md`

OpenAI smoke outputs use fixed filenames and overwrite prior runs in `tests/eval/out`.

## Notes

- Retrieval uses a lightweight reranker after hybrid search to boost relevance before
  selecting the final chunks for RAG. It is enabled by default and can be disabled by
  setting `RERANK_ENABLED=false`. Fake provider runs deterministically for eval/smoke.
- Grouped query endpoints apply source-aware retrieval when `PER_SOURCE_RETRIEVAL_LIMIT`
  is set and `source_ids` are provided.
- Ingestion happens asynchronously via Celery. Source status transitions: `UPLOADED` → `PROCESSING` → `READY` or `FAILED`.
- If a query cannot be answered with retrieved context, the API returns `insufficient evidence` with suggested follow-ups.
- `/query` returns answers with citations only; `/query/verified` adds claim-level verdicts and evidence snippets.
- `/query/verified/highlights` adds evidence highlight spans (start/end offsets and highlight_text) per evidence item.
- `/query/verified/grouped/highlights` combines highlights with grouped citations.
- Verified responses include `verification_summary` with verdict counts and an `overall_verdict`, plus `answer_style`.
  The `answer_style` field is also mirrored inside `verification_summary` and always matches the top-level value.
  If contradictions are detected, answers are prefixed with: "Contradictions detected in the source material."
  The body is rewritten into support/conflict/unsupported sections driven by claim verdicts.
- Citation and evidence snippets include snippet_start/snippet_end offsets relative to full chunk text.
  When chunk char offsets are available, absolute_start/absolute_end provide offsets within the
  original source text; fields may be null if spans are unavailable.
- Verification runs deterministically when `AI_PROVIDER=fake`.
- Highlight spans are best-effort and refer to indices in the full chunk text stored for the source.
  Highlight spans remain claim-specific and are independent from snippet offsets.
