from packages.shared_db.chunking import chunk_pages


def test_chunk_pages_creates_chunks() -> None:
    pages = [
        (1, "A" * 3000),
        (2, "B" * 3000),
    ]
    chunks = chunk_pages(pages, target_chars=2500, overlap_chars=200)

    assert chunks
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2
