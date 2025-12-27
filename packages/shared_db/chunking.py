from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkPayload:
    chunk_index: int
    page_start: int | None
    page_end: int | None
    text: str


def normalize_text(raw: str) -> str:
    lines = [line.strip() for line in raw.splitlines()]
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            cleaned.append(line)
        else:
            blank_run += 1
            if blank_run <= 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()


def build_page_ranges(pages: list[tuple[int, str]]) -> tuple[str, list[tuple[int, int, int]]]:
    full_text = ""
    ranges: list[tuple[int, int, int]] = []
    cursor = 0
    for page_num, page_text in pages:
        if not page_text:
            continue
        text_with_sep = page_text + "\n\n"
        start = cursor
        full_text += text_with_sep
        cursor += len(text_with_sep)
        end = cursor
        ranges.append((page_num, start, end))
    return full_text, ranges


def get_page_span(ranges: list[tuple[int, int, int]], start: int, end: int) -> tuple[int | None, int | None]:
    hits = [page_num for page_num, r_start, r_end in ranges if r_start < end and r_end > start]
    if not hits:
        return None, None
    return hits[0], hits[-1]


def chunk_pages(
    pages: list[tuple[int, str]], target_chars: int, overlap_chars: int
) -> list[ChunkPayload]:
    full_text, ranges = build_page_ranges(pages)
    if not full_text:
        return []

    chunks: list[ChunkPayload] = []
    start = 0
    index = 0
    text_len = len(full_text)

    while start < text_len:
        end = min(start + target_chars, text_len)
        if end < text_len:
            slice_text = full_text[start:end]
            last_space = slice_text.rfind(" ")
            if last_space > int(target_chars * 0.6):
                end = start + last_space
        chunk_text = full_text[start:end].strip()
        if chunk_text:
            page_start, page_end = get_page_span(ranges, start, end)
            chunks.append(
                ChunkPayload(
                    chunk_index=index,
                    page_start=page_start,
                    page_end=page_end,
                    text=chunk_text,
                )
            )
            index += 1

        if end >= text_len:
            break
        next_start = max(end - overlap_chars, 0)
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks
