# Changelog

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
