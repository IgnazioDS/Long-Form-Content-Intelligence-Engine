from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from _common.api_client import (  # noqa: E402
    fixture_pdf_path,
    get_api_headers,
    get_base_url,
    get_debug_chunk_ids,
    upload_source,
    wait_for_source,
)


def main() -> None:
    pdf_path = fixture_pdf_path()
    base_url = get_base_url()
    attempts = int(os.getenv("SMOKE_HEALTH_ATTEMPTS", "30"))
    sleep_s = float(os.getenv("SMOKE_HEALTH_SLEEP_S", "2"))
    debug_attempts = int(os.getenv("SMOKE_DEBUG_ATTEMPTS", "10"))
    debug_sleep_s = float(os.getenv("SMOKE_DEBUG_SLEEP_S", "1"))

    api_headers = get_api_headers()
    has_api_key = bool(api_headers.get("X-API-Key"))
    with httpx.Client(timeout=30.0, headers=api_headers) as client:
        health_url = f"{base_url}/health"
        if not _wait_for_health(client, health_url, attempts, sleep_s):
            if _start_stack_if_needed(base_url):
                if not _wait_for_health(client, health_url, attempts, sleep_s):
                    raise RuntimeError(
                        f"API failed to become healthy at {health_url} after "
                        "starting the stack. Check `docker compose logs`."
                    ) from None
            else:
                raise RuntimeError(
                    f"API failed to become healthy at {health_url}. "
                    "Ensure the API is running (e.g., "
                    "`AI_PROVIDER=fake DEBUG=true docker compose up --build -d`) "
                    "or set `SMOKE_AUTO_START=1` to let the smoke test start it."
                ) from None

        payload = upload_source(client, base_url, pdf_path, title="Smoke Test Fixture")
        source_id = payload["id"]

        wait_for_source(client, base_url, source_id)

        debug_checks_enabled = True
        try:
            chunk_ids = _get_debug_chunk_ids_with_retry(
                client, base_url, source_id, debug_attempts, debug_sleep_s
            )
        except RuntimeError as exc:
            if "DEBUG=true is required" not in str(exc):
                raise
            if not has_api_key:
                debug_checks_enabled = False
                chunk_ids = []
                print(
                    "Skipping debug chunk validation; API_KEY is not set. "
                    "Set API_KEY to enable debug endpoints."
                )
            elif _ensure_debug_endpoints(base_url):
                chunk_ids = _get_debug_chunk_ids_with_retry(
                    client, base_url, source_id, debug_attempts, debug_sleep_s
                )
            elif _skip_debug_checks():
                debug_checks_enabled = False
                chunk_ids = []
                print("Skipping debug chunk validation; DEBUG endpoints are unavailable.")
            else:
                raise RuntimeError(
                    "DEBUG endpoints are unavailable. Restart the API with DEBUG=true "
                    "or set SMOKE_SKIP_DEBUG=1 to skip chunk validation."
                ) from None

        if debug_checks_enabled and not chunk_ids:
            raise RuntimeError("No chunks found for source")

        query_payload = {"question": "What is this document about?", "source_ids": [source_id]}
        query_response = client.post(f"{base_url}/query", json=query_payload)
        query_response.raise_for_status()
        query_data = query_response.json()

        citations = query_data.get("citations", [])
        if not citations:
            raise RuntimeError("Query returned no citations")
        for citation in citations:
            if debug_checks_enabled:
                chunk_id = citation.get("chunk_id")
                if chunk_id not in chunk_ids:
                    raise RuntimeError(f"Citation chunk_id not found: {chunk_id}")
            if citation.get("source_id") != source_id:
                raise RuntimeError("Citation source_id does not match request")

    print("Smoke test passed")


def _wait_for_health(
    client: httpx.Client, health_url: str, attempts: int, sleep_s: float
) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            health = client.get(health_url)
            health.raise_for_status()
            return True
        except httpx.HTTPError:
            if attempt == attempts:
                return False
            time.sleep(sleep_s)
    return False


def _get_debug_chunk_ids_with_retry(
    client: httpx.Client,
    base_url: str,
    source_id: str,
    attempts: int,
    sleep_s: float,
) -> list[str]:
    for attempt in range(1, attempts + 1):
        try:
            return get_debug_chunk_ids(client, base_url, source_id)
        except httpx.HTTPError:
            if attempt == attempts:
                raise
            time.sleep(sleep_s)
    return []


def _start_stack_if_needed(base_url: str) -> bool:
    auto_start = os.getenv("SMOKE_AUTO_START", "1").strip().lower() in {"1", "true", "yes"}
    if not auto_start:
        return False
    if not _is_local_base_url(base_url):
        return False
    print("API not healthy yet; starting local docker compose stack...")
    try:
        subprocess.run(["docker", "compose", "up", "--build", "-d"], check=True)
        return True
    except FileNotFoundError:
        print("docker compose not found; start the stack manually.")
    except subprocess.CalledProcessError as exc:
        print(f"docker compose up failed (exit {exc.returncode}); start manually.")
    return False


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = parsed.hostname
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _ensure_debug_endpoints(base_url: str) -> bool:
    if not _is_local_base_url(base_url):
        return False
    if os.getenv("SMOKE_AUTO_START", "1").strip().lower() not in {"1", "true", "yes"}:
        return False
    print("DEBUG endpoints unavailable; restarting local stack with DEBUG=true...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "--build", "-d"],
            check=True,
            env={**os.environ, "DEBUG": "true"},
        )
        return True
    except FileNotFoundError:
        print("docker compose not found; restart the stack manually.")
    except subprocess.CalledProcessError as exc:
        print(f"docker compose up failed (exit {exc.returncode}); restart manually.")
    return False


def _skip_debug_checks() -> bool:
    return os.getenv("SMOKE_SKIP_DEBUG", "").strip().lower() in {"1", "true", "yes"}


if __name__ == "__main__":
    main()
