# Changelog

## Unreleased

### Included
- Persisted answer read endpoints + legacy-tolerant hydration + contract tests + consistency logging.
- Added REQUIRE_API_KEY startup enforcement to prevent running without an API key in production.
- Hardened debug routes to mount only when `DEBUG=true` and hide them from OpenAPI otherwise.
- Added rate limit backend guardrails to prevent unsafe in-memory limiting in production or multi-worker modes.

## v0.1.0-mvp

### Included
- PDF ingestion with async processing and chunking.
- pgvector-backed embeddings storage and cosine similarity search.
- Hybrid retrieval (vector similarity + Postgres full-text search).
- Grounded RAG with chunk ID citations and "insufficient evidence" fallback.
- `AI_PROVIDER=fake` support for deterministic local/smoke runs.
- Docker smoke test flow via `make smoke`.

### Known Limitations
- Only PDF uploads are supported.
- No authentication or authorization layer.
- Provider support is limited to OpenAI or the fake provider.
- API-only service; no web UI.
- Single-node Postgres/Redis configuration expectations.
