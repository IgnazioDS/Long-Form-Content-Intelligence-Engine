from __future__ import annotations

from datetime import datetime
from enum import Enum
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
    rerank: bool | None = None


class CitationOut(BaseModel):
    chunk_id: UUID
    source_id: UUID
    source_title: str | None
    page_start: int | None
    page_end: int | None
    snippet: str
    model_config = ConfigDict(from_attributes=True)


class CitationGroupOut(BaseModel):
    source_id: UUID
    source_title: str | None
    citations: list[CitationOut]
    model_config = ConfigDict(from_attributes=True)


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    model_config = ConfigDict(from_attributes=True)


class QueryGroupedResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    citation_groups: list[CitationGroupOut]
    model_config = ConfigDict(from_attributes=True)


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    WEAK_SUPPORT = "WEAK_SUPPORT"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    CONFLICTING = "CONFLICTING"


class EvidenceRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    RELATED = "RELATED"


class EvidenceOut(BaseModel):
    chunk_id: UUID
    relation: EvidenceRelation
    snippet: str
    model_config = ConfigDict(from_attributes=True)


class EvidenceHighlightOut(BaseModel):
    chunk_id: UUID
    relation: EvidenceRelation
    snippet: str
    highlight_start: int | None
    highlight_end: int | None
    highlight_text: str | None
    absolute_start: int | None
    absolute_end: int | None
    model_config = ConfigDict(from_attributes=True)


class ClaimOut(BaseModel):
    claim_text: str
    verdict: Verdict
    support_score: float
    contradiction_score: float
    evidence: list[EvidenceOut]
    model_config = ConfigDict(from_attributes=True)


class ClaimHighlightOut(BaseModel):
    claim_text: str
    verdict: Verdict
    support_score: float
    contradiction_score: float
    evidence: list[EvidenceHighlightOut]
    model_config = ConfigDict(from_attributes=True)


class QueryVerifiedResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    claims: list[ClaimOut]
    model_config = ConfigDict(from_attributes=True)


class QueryVerifiedGroupedResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    claims: list[ClaimOut]
    citation_groups: list[CitationGroupOut]
    model_config = ConfigDict(from_attributes=True)


class QueryVerifiedHighlightsResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    claims: list[ClaimHighlightOut]
    model_config = ConfigDict(from_attributes=True)


class QueryVerifiedGroupedHighlightsResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    claims: list[ClaimHighlightOut]
    citation_groups: list[CitationGroupOut]
    model_config = ConfigDict(from_attributes=True)
