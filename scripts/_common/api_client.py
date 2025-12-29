from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


def get_base_url(override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    base_url = os.getenv("API_BASE_URL") or DEFAULT_BASE_URL
    return base_url.rstrip("/")


def fixture_pdf_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "sample.pdf"


def list_sources(client: httpx.Client, base_url: str) -> list[dict[str, Any]]:
    response = client.get(f"{base_url}/sources")
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [item for item in sources if isinstance(item, dict)]


def find_source_by_filename(
    sources: list[dict[str, Any]], filename: str
) -> dict[str, Any] | None:
    for source in sources:
        if source.get("original_filename") == filename:
            return source
    return None


def wait_for_source(
    client: httpx.Client, base_url: str, source_id: str, timeout_s: int = 60
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sources = list_sources(client, base_url)
        match = next(
            (
                item
                for item in sources
                if isinstance(item, dict) and str(item.get("id")) == str(source_id)
            ),
            None,
        )
        if match:
            status = match.get("status")
            if status == "READY":
                return cast(dict[str, Any], match)
            if status == "FAILED":
                raise RuntimeError(f"Source failed ingestion: {match}")
        time.sleep(2)
    raise TimeoutError("Timed out waiting for source to become READY")


def upload_source(
    client: httpx.Client,
    base_url: str,
    pdf_path: Path,
    title: str | None = None,
) -> dict[str, Any]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing fixture: {pdf_path}")

    with pdf_path.open("rb") as handle:
        files = {"file": (pdf_path.name, handle, "application/pdf")}
        data = {"title": title} if title else {}
        response = client.post(f"{base_url}/sources/upload", files=files, data=data)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())


def delete_source(client: httpx.Client, base_url: str, source_id: str) -> None:
    response = client.delete(f"{base_url}/sources/{source_id}")
    if response.status_code == 404:
        return
    response.raise_for_status()


def get_debug_chunk_ids(
    client: httpx.Client, base_url: str, source_id: str
) -> list[str]:
    response = client.get(f"{base_url}/debug/sources/{source_id}/chunks")
    if response.status_code == 404:
        raise RuntimeError("DEBUG=true is required to access debug endpoints")
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    chunk_ids = payload.get("chunk_ids", [])
    if not isinstance(chunk_ids, list):
        return []
    return [str(cid) for cid in chunk_ids]


def get_debug_chunk_text(client: httpx.Client, base_url: str, chunk_id: str) -> str:
    payload = get_debug_chunk_info(client, base_url, chunk_id)
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("Invalid debug chunk response")
    return text


def get_debug_chunk_info(
    client: httpx.Client, base_url: str, chunk_id: str
) -> dict[str, Any]:
    response = client.get(f"{base_url}/debug/chunks/{chunk_id}")
    if response.status_code == 404:
        raise RuntimeError("DEBUG=true is required to access debug endpoints")
    response.raise_for_status()
    return cast(dict[str, Any], response.json())
