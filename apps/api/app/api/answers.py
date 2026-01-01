from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.api._answers_hydration import hydrate_answer_payload
from apps.api.app.deps import get_session
from apps.api.app.schemas import QueryVerifiedResponse
from apps.api.app.security import require_api_key
from packages.shared_db.models import Answer

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/answers/{answer_id}", response_model=QueryVerifiedResponse)
def get_answer(
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    hydrated = hydrate_answer_payload(answer_row, grouped=False, highlights=False)

    return QueryVerifiedResponse(
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        verification_summary=hydrated["verification_summary"],
    )
