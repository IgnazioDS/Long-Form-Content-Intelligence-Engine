from __future__ import annotations

import logging
import uuid

import fitz
from celery import shared_task
from sqlalchemy import func

from packages.shared_db.chunking import chunk_pages, normalize_text
from packages.shared_db.models import Chunk, Source, SourceStatus
from packages.shared_db.openai_client import embed_texts
from packages.shared_db.session import SessionLocal
from packages.shared_db.settings import settings
from packages.shared_db.storage import source_path

logger = logging.getLogger(__name__)


@shared_task(name="services.ingest.tasks.ingest_source")
def ingest_source(source_id: str) -> None:
    session = SessionLocal()
    source: Source | None = None
    try:
        source_uuid = uuid.UUID(source_id)
        source = session.get(Source, source_uuid)
        if source is None:
            logger.error("Source not found: %s", source_id)
            return

        source.status = SourceStatus.PROCESSING.value
        source.error = None
        session.commit()

        path = source_path(source_id)
        if settings.max_pdf_bytes > 0:
            file_size = path.stat().st_size
            if file_size > settings.max_pdf_bytes:
                max_mb = settings.max_pdf_bytes / (1024 * 1024)
                raise ValueError(
                    f"PDF exceeds max size of {max_mb:.1f} MB. "
                    "Please upload a smaller file."
                )
        pages: list[tuple[int, str]] = []
        with fitz.open(str(path)) as doc:
            if getattr(doc, "is_encrypted", False) or getattr(doc, "needs_pass", False):
                raise ValueError("PDF is encrypted. Please upload an unencrypted PDF.")
            if settings.max_pdf_pages > 0 and doc.page_count > settings.max_pdf_pages:
                raise ValueError(
                    f"PDF exceeds max page count of {settings.max_pdf_pages}. "
                    "Please upload a shorter document."
                )
            for page_index, page in enumerate(doc, start=1):
                text = normalize_text(page.get_text())
                if text:
                    pages.append((page_index, text))

        chunks = chunk_pages(pages, settings.chunk_char_target, settings.chunk_char_overlap)
        if not chunks:
            raise ValueError(
                "No extractable text found. If this is a scanned PDF, run OCR and re-upload."
            )

        embeddings = embed_texts([chunk.text for chunk in chunks])

        session.query(Chunk).filter(Chunk.source_id == source.id).delete(
            synchronize_session=False
        )
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            session.add(
                Chunk(
                    source_id=source.id,
                    chunk_index=chunk.chunk_index,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    section_path=[],
                    text=chunk.text,
                    tsv=func.to_tsvector("english", chunk.text),
                    embedding=embedding,
                )
            )

        source.status = SourceStatus.READY.value
        source.error = None
        session.commit()
        logger.info("Ingestion complete for source %s", source_id)
    except Exception as exc:
        logger.exception("Failed ingestion for source %s", source_id)
        session.rollback()
        if source is not None:
            error_text = str(exc).strip() or exc.__class__.__name__
            source.status = SourceStatus.FAILED.value
            source.error = error_text[:500]
            session.commit()
    finally:
        session.close()
