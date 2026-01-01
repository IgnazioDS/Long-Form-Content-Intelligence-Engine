from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.security import require_api_key
from packages.shared_db.models import Chunk
from packages.shared_db.settings import settings

router = APIRouter(dependencies=[Depends(require_api_key)])


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
