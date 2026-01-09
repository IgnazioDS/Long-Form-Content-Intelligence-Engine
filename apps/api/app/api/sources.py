from __future__ import annotations

import os
import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import SourceIngestRequest, SourceListOut, SourceOut
from apps.api.app.security import require_api_key
from packages.shared_db.models import Source, SourceStatus
from packages.shared_db.settings import settings
from packages.shared_db.storage import source_path
from packages.shared_db.url_guard import is_url_safe
from services.ingest.worker import celery_app

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/sources/upload", response_model=SourceOut)
def upload_source(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    session: Session = Depends(get_session),
) -> SourceOut:
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")
    max_bytes = settings.max_pdf_bytes
    if max_bytes > 0:
        try:
            file.file.seek(0, os.SEEK_END)
            size_bytes = file.file.tell()
            file.file.seek(0)
        except (OSError, ValueError):
            size_bytes = None
        if size_bytes is not None and size_bytes > max_bytes:
            raise HTTPException(status_code=413, detail="Upload too large")

    source = Source(
        title=title or filename,
        source_type="pdf",
        original_filename=filename,
        status=SourceStatus.UPLOADED.value,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    target_path = source_path(str(source.id), source.source_type)
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    task = celery_app.send_task("services.ingest.tasks.ingest_source", args=[str(source.id)])

    return SourceOut.model_validate(source).model_copy(update={"ingest_task_id": task.id})


@router.post("/sources/ingest", response_model=SourceOut)
def ingest_source(
    payload: SourceIngestRequest, session: Session = Depends(get_session)
) -> SourceOut:
    has_text = bool(payload.text and payload.text.strip())
    source_type = "text" if has_text else "url"
    title = payload.title or (str(payload.url) if payload.url else "Ingested text")
    if payload.url and not is_url_safe(str(payload.url)):
        raise HTTPException(status_code=400, detail="URL is not allowed")

    source = Source(
        title=title,
        source_type=source_type,
        original_filename=str(payload.url) if payload.url else None,
        status=SourceStatus.UPLOADED.value,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    target_path = source_path(str(source.id), source.source_type)
    if has_text:
        target_path.write_text(payload.text.strip(), encoding="utf-8")
    else:
        target_path.write_text(str(payload.url), encoding="utf-8")

    task = celery_app.send_task("services.ingest.tasks.ingest_source", args=[str(source.id)])

    return SourceOut.model_validate(source).model_copy(update={"ingest_task_id": task.id})


@router.get("/sources", response_model=SourceListOut)
def list_sources(
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    source_type: str | None = Query(None),
) -> SourceListOut:
    query = session.query(Source)
    if status:
        normalized = status.strip().upper()
        if normalized:
            query = query.filter(func.upper(Source.status) == normalized)
    if source_type:
        normalized = source_type.strip().lower()
        if normalized:
            query = query.filter(func.lower(Source.source_type) == normalized)
    total = query.count()
    sources = (
        query.order_by(Source.created_at.desc()).offset(offset).limit(limit).all()
    )
    return SourceListOut(
        sources=[SourceOut.model_validate(source) for source in sources],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/sources/{source_id}", response_model=SourceOut)
def delete_source(source_id: UUID, session: Session = Depends(get_session)) -> SourceOut:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    target_path = source_path(str(source_id), source.source_type)
    session.delete(source)
    session.commit()
    try:
        target_path.unlink(missing_ok=True)
    except OSError:
        pass
    return SourceOut.model_validate(source)
