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

## Required Environment Variables

- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `AI_PROVIDER` (`openai` or `fake`, default: `openai`)
- `DEBUG` (default: `false`)
- `DATABASE_URL`
- `REDIS_URL`
- `MAX_CHUNKS_PER_QUERY` (default: `8`)
- `RERANK_ENABLED` (default: `true`)
- `RERANK_CANDIDATES` (default: `30`)
- `RERANK_SNIPPET_CHARS` (default: `900`)
- `CHUNK_CHAR_TARGET` (default: `5000`)
- `CHUNK_CHAR_OVERLAP` (default: `800`)

## Optional Environment Variables

- `API_KEY` (if set, send `X-API-Key` on requests)
- `RATE_LIMIT_RPS` (default: `0`, disabled when `0`)
- `RATE_LIMIT_BURST` (default: `0`)
- `MMR_ENABLED` (default: `true`)
- `MMR_LAMBDA` (default: `0.7`)
- `MMR_CANDIDATES` (default: `30`)
- `MAX_PDF_BYTES` (default: `25000000`)
- `MAX_PDF_PAGES` (default: `300`)
- `LOG_LEVEL` (default: `INFO`)

## How to Run Locally

```bash
make up
```

The API will be available at `http://localhost:8000`.

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

List sources:
```bash
curl http://localhost:8000/sources
```

Query with RAG:
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main thesis?", "source_ids": ["YOUR_SOURCE_UUID"]}'
```

Query with verification:
```bash
curl -X POST http://localhost:8000/query/verified \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main thesis?", "source_ids": ["YOUR_SOURCE_UUID"]}'
```

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
7. Run the evidence integrity harness:
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

Outputs are written to:
- `scripts/eval/out/eval_results.json`
- `scripts/eval/out/eval_report.md`
- `scripts/eval/out/eval_verified_results.json`
- `scripts/eval/out/eval_verified_report.md`
- `scripts/eval/out/eval_multisource_results.json`
- `scripts/eval/out/eval_multisource_report.md`
- `tests/eval/out/eval_openai_smoke_results.json`
- `tests/eval/out/eval_openai_smoke_report.md`
- `tests/eval/out/eval_evidence_integrity_results.json`
- `tests/eval/out/eval_evidence_integrity_report.md`

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
  If contradictions are detected, answers are prefixed with: "Contradictions detected in the source material."
  The body is rewritten into support/conflict/unsupported sections driven by claim verdicts.
- Citation and evidence snippets include snippet_start/snippet_end offsets relative to full chunk text.
  When chunk char offsets are available, absolute_start/absolute_end provide offsets within the
  original source text; fields may be null if spans are unavailable.
- Verification runs deterministically when `AI_PROVIDER=fake`.
- Highlight spans are best-effort and refer to indices in the full chunk text stored for the source.
  Highlight spans remain claim-specific and are independent from snippet offsets.
