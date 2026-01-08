from __future__ import annotations

import html
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import fitz
import httpx
from celery import shared_task
from sqlalchemy import func

from packages.shared_db.chunking import chunk_pages, normalize_text
from packages.shared_db.models import Chunk, Source, SourceStatus
from packages.shared_db.openai_client import embed_texts
from packages.shared_db.session import SessionLocal
from packages.shared_db.settings import settings
from packages.shared_db.storage import source_path

logger = logging.getLogger(__name__)

_SOURCE_TYPE_PDF = "pdf"
_SOURCE_TYPE_TEXT = "text"
_SOURCE_TYPE_URL = "url"
_HTML_BLOCK_TAG_RE = re.compile(r"(?i)</?(?:br|p|div|li|h[1-6])[^>]*>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _normalize_source_type(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value or _SOURCE_TYPE_PDF


def _read_text_payload(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _strip_html(payload: str) -> str:
    cleaned = _HTML_BLOCK_TAG_RE.sub("\n", payload)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    return html.unescape(cleaned)


def _build_section_map(toc: list[list[object]], page_count: int) -> dict[int, list[str]]:
    if not toc or page_count <= 0:
        return {}
    entries: list[tuple[int, int, int, str]] = []
    for idx, item in enumerate(toc):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        level, title, page = item[:3]
        if not isinstance(level, int):
            continue
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue
        if page_num < 1 or page_num > page_count:
            continue
        title_text = str(title).strip()
        if not title_text:
            continue
        entries.append((page_num, idx, level, title_text))
    if not entries:
        return {}
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    stack: list[tuple[int, str]] = []
    section_by_page: dict[int, list[str]] = {}
    entry_idx = 0
    for page in range(1, page_count + 1):
        while entry_idx < len(entries) and entries[entry_idx][0] == page:
            _, _, level, title = entries[entry_idx]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            entry_idx += 1
        section_by_page[page] = [title for _, title in stack]
    return section_by_page


def _is_text_content(content_type: str) -> bool:
    if not content_type:
        return True
    ctype = content_type.split(";", 1)[0].strip().lower()
    return ctype.startswith("text/") or ctype in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
    }


def _fetch_url_text(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are supported.")
    response = httpx.get(url, timeout=20.0, follow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not _is_text_content(content_type):
        raise ValueError(f"Unsupported URL content-type: {content_type}")
    text = response.text
    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        text = _strip_html(text)
    return text


def _pages_from_text(text: str) -> list[tuple[int, str]]:
    cleaned = normalize_text(text)
    if not cleaned:
        raise ValueError("No extractable text found. Please provide a longer input.")
    return [(1, cleaned)]


def _section_path_for_chunk(
    chunk: object, section_by_page: dict[int, list[str]]
) -> list[str]:
    if not section_by_page:
        return []
    page_start = getattr(chunk, "page_start", None)
    if isinstance(page_start, int) and page_start in section_by_page:
        return section_by_page[page_start]
    page_end = getattr(chunk, "page_end", None)
    if isinstance(page_end, int) and page_end in section_by_page:
        return section_by_page[page_end]
    return []


@shared_task(
    bind=True,
    name="services.ingest.tasks.ingest_source",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def ingest_source(self, source_id: str) -> None:
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

        source_type = _normalize_source_type(source.source_type)
        pages: list[tuple[int, str]] = []
        section_by_page: dict[int, list[str]] = {}
        if source_type == _SOURCE_TYPE_PDF:
            path = source_path(source_id, source_type)
            if settings.max_pdf_bytes > 0:
                file_size = path.stat().st_size
                if file_size > settings.max_pdf_bytes:
                    max_mb = settings.max_pdf_bytes / (1024 * 1024)
                    raise ValueError(
                        f"PDF exceeds max size of {max_mb:.1f} MB. "
                        "Please upload a smaller file."
                    )
            with fitz.open(str(path)) as doc:
                section_by_page = _build_section_map(
                    doc.get_toc(simple=True) or [], doc.page_count
                )
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
        elif source_type == _SOURCE_TYPE_TEXT:
            path = source_path(source_id, source_type)
            pages = _pages_from_text(_read_text_payload(path))
        elif source_type == _SOURCE_TYPE_URL:
            path = source_path(source_id, source_type)
            url_payload = _read_text_payload(path).strip()
            if not url_payload:
                raise ValueError("Missing URL payload for source.")
            pages = _pages_from_text(_fetch_url_text(url_payload))
        else:
            raise ValueError(f"Unsupported source_type: {source_type}")

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
            section_path = _section_path_for_chunk(chunk, section_by_page)
            session.add(
                Chunk(
                    source_id=source.id,
                    chunk_index=chunk.chunk_index,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    section_path=section_path,
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
            should_retry = isinstance(exc, httpx.HTTPError) and (
                self.request.retries < self.max_retries
            )
            if should_retry:
                source.status = SourceStatus.PROCESSING.value
                source.error = None
                session.commit()
            else:
                source.status = SourceStatus.FAILED.value
                source.error = error_text[:500]
                session.commit()
            if should_retry:
                raise
    finally:
        session.close()
