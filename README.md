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

1. Start the stack:
   ```bash
   AI_PROVIDER=fake DEBUG=true docker compose up --build
   ```
2. Run the eval harness:
   ```bash
   make eval
   ```

Outputs are written to:
- `scripts/eval/out/eval_results.json`
- `scripts/eval/out/eval_report.md`

## Notes

- Retrieval uses a lightweight reranker after hybrid search to boost relevance before
  selecting the final chunks for RAG. It is enabled by default and can be disabled by
  setting `RERANK_ENABLED=false`. Fake provider runs deterministically for eval/smoke.
- Ingestion happens asynchronously via Celery. Source status transitions: `UPLOADED` → `PROCESSING` → `READY` or `FAILED`.
- If a query cannot be answered with retrieved context, the API returns `insufficient evidence` with suggested follow-ups.
