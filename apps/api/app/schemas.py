from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class SourceOut(BaseModel):
    id: UUID
    title: str | None
    source_type: str
    original_filename: str | None
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SourceListOut(BaseModel):
    sources: list[SourceOut]
    model_config = ConfigDict(from_attributes=True)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3)
    source_ids: list[UUID] | None = None


class CitationOut(BaseModel):
    chunk_id: UUID
    source_id: UUID
    source_title: str | None
    page_start: int | None
    page_end: int | None
    snippet: str
    model_config = ConfigDict(from_attributes=True)


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    model_config = ConfigDict(from_attributes=True)
