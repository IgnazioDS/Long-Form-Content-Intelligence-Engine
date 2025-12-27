import uuid

from pytest import MonkeyPatch

from apps.api.app.services import rag
from apps.api.app.services.retrieval import RetrievedChunk


def test_generate_answer_returns_citations(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.uuid4()
    chunks = [
        RetrievedChunk(
            chunk_id=chunk_id,
            source_id=uuid.uuid4(),
            source_title="Doc",
            page_start=1,
            page_end=2,
            text="Some text",
            score=1.0,
        )
    ]

    def fake_call_llm(
        question: str, context: str, allowed_ids: list[str], strict: bool
    ) -> dict[str, object]:
        return {"answer": "Answer", "citations": [str(chunk_id)], "follow_ups": []}

    monkeypatch.setattr(rag, "_call_llm", fake_call_llm)

    answer, citations = rag.generate_answer("Question", chunks)

    assert answer == "Answer"
    assert citations == [chunk_id]
