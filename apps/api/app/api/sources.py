from __future__ import annotations

import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import SourceListOut, SourceOut
from apps.api.app.security import require_api_key
from packages.shared_db.models import Chunk, Source, SourceStatus
from packages.shared_db.settings import settings
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

    target_path = source_path(str(source.id))
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

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
    session.delete(source)
    session.commit()
    try:
        source_path(str(source_id)).unlink(missing_ok=True)
    except OSError:
        pass
    return SourceOut.model_validate(source)


@router.get("/debug/sources/{source_id}/chunks")
def debug_list_chunks(
    source_id: UUID, session: Session = Depends(get_session)
) -> dict[str, object]:
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    rows = (
        session.query(Chunk.id)
        .filter(Chunk.source_id == source_id)
        .order_by(Chunk.chunk_index)
        .all()
    )
    return {"source_id": str(source_id), "chunk_ids": [str(row.id) for row in rows]}


@router.get("/debug/chunks/{chunk_id}")
def debug_get_chunk(
    chunk_id: UUID, session: Session = Depends(get_session)
) -> dict[str, object]:
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    chunk = session.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {
        "chunk_id": str(chunk.id),
        "source_id": str(chunk.source_id),
        "text": chunk.text,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
    }
