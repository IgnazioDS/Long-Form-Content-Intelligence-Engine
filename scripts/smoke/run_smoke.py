from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from _common.api_client import (  # noqa: E402
    fixture_pdf_path,
    get_base_url,
    get_debug_chunk_ids,
    upload_source,
    wait_for_source,
)


def main() -> None:
    pdf_path = fixture_pdf_path()
    base_url = get_base_url()

    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()

        payload = upload_source(client, base_url, pdf_path, title="Smoke Test Fixture")
        source_id = payload["id"]

        wait_for_source(client, base_url, source_id)

        chunk_ids = get_debug_chunk_ids(client, base_url, source_id)
        if not chunk_ids:
            raise RuntimeError("No chunks found for source")

        query_payload = {"question": "What is this document about?", "source_ids": [source_id]}
        query_response = client.post(f"{base_url}/query", json=query_payload)
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
