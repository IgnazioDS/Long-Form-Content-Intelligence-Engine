from __future__ import annotations

import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import SourceIngestRequest, SourceListOut, SourceOut
from apps.api.app.security import require_api_key
from packages.shared_db.models import Source, SourceStatus
from packages.shared_db.storage import source_path
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

    celery_app.send_task("services.ingest.tasks.ingest_source", args=[str(source.id)])

    return SourceOut.model_validate(source)


@router.post("/sources/ingest", response_model=SourceOut)
def ingest_source(
    payload: SourceIngestRequest, session: Session = Depends(get_session)
) -> SourceOut:
    has_text = bool(payload.text and payload.text.strip())
    source_type = "text" if has_text else "url"
    title = payload.title or (str(payload.url) if payload.url else "Ingested text")

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

    celery_app.send_task("services.ingest.tasks.ingest_source", args=[str(source.id)])

    return SourceOut.model_validate(source)


@router.get("/sources", response_model=SourceListOut)
def list_sources(session: Session = Depends(get_session)) -> SourceListOut:
    sources = session.query(Source).order_by(Source.created_at.desc()).all()
    return SourceListOut(sources=[SourceOut.model_validate(source) for source in sources])


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
