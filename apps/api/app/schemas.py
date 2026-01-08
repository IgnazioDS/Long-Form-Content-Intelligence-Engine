from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
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
    snippet_start: int | None = None
    snippet_end: int | None = None
    absolute_start: int | None = None
    absolute_end: int | None = None
    model_config = ConfigDict(from_attributes=True)


class CitationGroupOut(BaseModel):
    source_id: UUID
    source_title: str | None
    citations: list[CitationOut]
    model_config = ConfigDict(from_attributes=True)


class QueryResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
    answer: str
    citations: list[CitationOut]
    model_config = ConfigDict(from_attributes=True)


class QueryGroupedResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
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
    snippet_start: int | None = None
    snippet_end: int | None = None
    absolute_start: int | None = None
    absolute_end: int | None = None
    model_config = ConfigDict(from_attributes=True)


class EvidenceHighlightOut(BaseModel):
    chunk_id: UUID
    relation: EvidenceRelation
    snippet: str
    snippet_start: int | None = None
    snippet_end: int | None = None
    highlight_start: int | None
    highlight_end: int | None
    highlight_text: str | None
    absolute_start: int | None = None
    absolute_end: int | None = None
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


class AnswerStyle(str, Enum):
    ORIGINAL = "ORIGINAL"
    CONFLICT_REWRITTEN = "CONFLICT_REWRITTEN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class VerificationOverallVerdict(str, Enum):
    OK = "OK"
    HAS_CONTRADICTIONS = "HAS_CONTRADICTIONS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class VerificationSummaryOut(BaseModel):
    supported_count: int
    weak_support_count: int
    unsupported_count: int
    contradicted_count: int
    conflicting_count: int
    has_contradictions: bool
    overall_verdict: VerificationOverallVerdict
    answer_style: AnswerStyle
    model_config = ConfigDict(from_attributes=True)


class QueryVerifiedResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
    answer: str
    answer_style: AnswerStyle
    citations: list[CitationOut]
    claims: list[ClaimOut]
    verification_summary: VerificationSummaryOut
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _validate_answer_style(self) -> QueryVerifiedResponse:
        summary_style = self.verification_summary.answer_style
        if summary_style is None:
            raise ValueError("verification_summary.answer_style is required")
        if summary_style != self.answer_style:
            raise ValueError("verification_summary.answer_style must match answer_style")
        return self


class QueryVerifiedGroupedResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
    answer: str
    answer_style: AnswerStyle
    citations: list[CitationOut]
    claims: list[ClaimOut]
    citation_groups: list[CitationGroupOut]
    verification_summary: VerificationSummaryOut
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _validate_answer_style(self) -> QueryVerifiedGroupedResponse:
        summary_style = self.verification_summary.answer_style
        if summary_style is None:
            raise ValueError("verification_summary.answer_style is required")
        if summary_style != self.answer_style:
            raise ValueError("verification_summary.answer_style must match answer_style")
        return self


class QueryVerifiedHighlightsResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
    answer: str
    answer_style: AnswerStyle
    citations: list[CitationOut]
    claims: list[ClaimHighlightOut]
    verification_summary: VerificationSummaryOut
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _validate_answer_style(self) -> QueryVerifiedHighlightsResponse:
        summary_style = self.verification_summary.answer_style
        if summary_style is None:
            raise ValueError("verification_summary.answer_style is required")
        if summary_style != self.answer_style:
            raise ValueError("verification_summary.answer_style must match answer_style")
        return self


class QueryVerifiedGroupedHighlightsResponse(BaseModel):
    answer_id: UUID | None = None
    query_id: UUID | None = None
    answer: str
    answer_style: AnswerStyle
    citations: list[CitationOut]
    claims: list[ClaimHighlightOut]
    citation_groups: list[CitationGroupOut]
    verification_summary: VerificationSummaryOut
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _validate_answer_style(self) -> QueryVerifiedGroupedHighlightsResponse:
        summary_style = self.verification_summary.answer_style
        if summary_style is None:
            raise ValueError("verification_summary.answer_style is required")
        if summary_style != self.answer_style:
            raise ValueError("verification_summary.answer_style must match answer_style")
        return self
