from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
PDF_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample.pdf"


def wait_for_source(
    client: httpx.Client, source_id: str, timeout_s: int = 60
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        response = client.get(f"{BASE_URL}/sources")
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        sources = payload.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        match = next(
            (
                item
                for item in sources
                if isinstance(item, dict) and item.get("id") == source_id
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


def main() -> None:
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {PDF_PATH}")

    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{BASE_URL}/health")
        health.raise_for_status()

        with PDF_PATH.open("rb") as handle:
            files = {"file": (PDF_PATH.name, handle, "application/pdf")}
            data = {"title": "Smoke Test Fixture"}
            response = client.post(f"{BASE_URL}/sources/upload", files=files, data=data)
            response.raise_for_status()
            payload = response.json()
            source_id = payload["id"]

        wait_for_source(client, source_id)

        debug_response = client.get(f"{BASE_URL}/debug/sources/{source_id}/chunks")
        if debug_response.status_code == 404:
            raise RuntimeError("DEBUG=true is required to access debug endpoints")
        debug_response.raise_for_status()
        chunk_ids = debug_response.json().get("chunk_ids", [])
        if not chunk_ids:
            raise RuntimeError("No chunks found for source")

        query_payload = {"question": "What is this document about?", "source_ids": [source_id]}
        query_response = client.post(f"{BASE_URL}/query", json=query_payload)
        query_response.raise_for_status()
        query_data = query_response.json()

        citations = query_data.get("citations", [])
        if not citations:
            raise RuntimeError("Query returned no citations")
        for citation in citations:
            chunk_id = citation.get("chunk_id")
            if chunk_id not in chunk_ids:
                raise RuntimeError(f"Citation chunk_id not found: {chunk_id}")
            if citation.get("source_id") != source_id:
                raise RuntimeError("Citation source_id does not match request")

    print("Smoke test passed")


if __name__ == "__main__":
    main()
