import uuid

from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from apps.api.app.services import reranker
from apps.api.app.services.retrieval import RetrievedChunk, _apply_source_filter
from packages.shared_db.models import Chunk
from packages.shared_db.settings import settings


def _make_chunk(chunk_id: uuid.UUID, score: float, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=uuid.uuid4(),
        source_title="Doc",
        page_start=1,
        page_end=2,
        text=text,
        score=score,
    )


def test_rerank_fake_deterministic_changes_order(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "rerank_candidates", 4)

    chunks = [
        _make_chunk(uuid.UUID("00000000-0000-0000-0000-000000000001"), 3.0, "Alpha"),
        _make_chunk(uuid.UUID("00000000-0000-0000-0000-000000000002"), 2.0, "Bravo"),
        _make_chunk(uuid.UUID("00000000-0000-0000-0000-000000000003"), 1.0, "Charlie"),
        _make_chunk(uuid.UUID("00000000-0000-0000-0000-000000000004"), 0.5, "Delta"),
    ]
    pre_sorted = sorted(chunks, key=lambda item: item.score, reverse=True)
    question_candidates = ["What is this about?", "Summarize the key points.", "Explain."]

    expected: tuple[str, list[RetrievedChunk]] | None = None
    for question in question_candidates:
        expected_order = sorted(
            chunks,
            key=lambda item: reranker._fake_score(
                question,
                str(item.chunk_id),
                reranker._clean_snippet(item.text, 120),
            ),
            reverse=True,
        )
        if [item.chunk_id for item in expected_order] != [
            item.chunk_id for item in pre_sorted
        ]:
            expected = (question, expected_order)
            break

    assert expected is not None
    question, expected_order = expected
    result_one = reranker.rerank_chunks(question, chunks, 120)
    result_two = reranker.rerank_chunks(question, chunks, 120)

    assert [item.chunk_id for item in result_one] == [
        item.chunk_id for item in expected_order
    ]
    assert [item.chunk_id for item in result_two] == [
        item.chunk_id for item in expected_order
    ]


def test_rerank_disabled_preserves_hybrid_order(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rerank_enabled", False)
    monkeypatch.setattr(settings, "rerank_candidates", 3)

    chunks = [
        _make_chunk(uuid.uuid4(), 0.2, "One"),
        _make_chunk(uuid.uuid4(), 2.5, "Two"),
        _make_chunk(uuid.uuid4(), 1.1, "Three"),
    ]
    result = reranker.rerank_chunks("Question", chunks, 50)
    expected = sorted(chunks, key=lambda item: item.score, reverse=True)

    assert [item.chunk_id for item in result] == [item.chunk_id for item in expected]


def test_rerank_output_ids_match_input(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "rerank_candidates", 3)

    chunks = [
        _make_chunk(uuid.uuid4(), 1.0, "One"),
        _make_chunk(uuid.uuid4(), 0.9, "Two"),
        _make_chunk(uuid.uuid4(), 0.8, "Three"),
    ]
    result = reranker.rerank_chunks("Question", chunks, 50)

    assert {item.chunk_id for item in result} == {item.chunk_id for item in chunks}


def test_apply_source_filter_includes_ids() -> None:
    source_id = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    stmt = _apply_source_filter(select(Chunk.id), [source_id])
    compiled = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "chunks.source_id" in compiled
    assert str(source_id) in compiled


def test_apply_source_filter_skips_when_empty() -> None:
    stmt = _apply_source_filter(select(Chunk.id), None)
    compiled = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "source_id" not in compiled
