from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT / "scripts"))

from _common.api_client import (  # noqa: E402
    delete_source,
    find_source_by_filename,
    fixture_pdf_path,
    get_base_url,
    get_debug_chunk_ids,
    get_debug_chunk_info,
    list_sources,
    upload_source,
    wait_for_source,
)

DATASET_PATH = Path(__file__).resolve().parent / "golden_evidence_integrity.json"
OUT_DIR = Path(__file__).resolve().parent / "out"
DEFAULT_READY_TIMEOUT_SECONDS = 60
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
POST_RETRY_LIMIT = 3
RETRY_BACKOFF_SECONDS = 0.5
HEALTH_POLL_INTERVAL_SECONDS = 2.0

INSUFFICIENT_EVIDENCE_PHRASES = (
    "insufficient evidence",
    "not enough evidence",
    "not enough information",
    "cannot answer",
    "no relevant information",
)


def get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is not None and value.strip():
        return value
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == name:
            cleaned = raw_value.strip().strip("\"'")
            return cleaned if cleaned else None
    return None


def require_openai_env() -> None:
    provider = (get_env_value("AI_PROVIDER") or "").strip().lower() or "openai"
    if provider != "openai":
        raise RuntimeError("AI_PROVIDER must be set to openai for eval-evidence-integrity")
    api_key = get_env_value("OPENAI_API_KEY") or ""
    if not api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is required for eval-evidence-integrity")


def load_dataset(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases_payload = payload
        fixture_name: str | None = None
    elif isinstance(payload, dict):
        cases_payload = payload.get("cases", [])
        fixture_raw = payload.get("fixture")
        fixture_name = str(fixture_raw).strip() if fixture_raw else None
    else:
        raise ValueError("Eval dataset must be a JSON list or object")

    if not isinstance(cases_payload, list):
        raise ValueError("Eval dataset cases must be a list")

    cases: list[dict[str, Any]] = []
    for item in cases_payload:
        if not isinstance(item, dict):
            raise ValueError("Each eval case must be an object")
        for key in ("id", "question", "expected_behavior"):
            if key not in item:
                raise ValueError(f"Missing required field: {key}")
        cases.append(item)
    return cases, fixture_name


def resolve_fixture_path(fixture_name: str | None) -> Path:
    if fixture_name:
        candidate = Path(fixture_name)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / "scripts" / "fixtures" / fixture_name
        if not candidate.exists():
            raise FileNotFoundError(f"Fixture not found: {candidate}")
        return candidate
    return fixture_pdf_path()


def get_git_commit() -> str | None:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.strip() or None


def contains_insufficient_evidence(answer: str) -> bool:
    lowered = answer.strip().lower()
    return any(phrase in lowered for phrase in INSUFFICIENT_EVIDENCE_PHRASES)


def post_with_retries(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    max_attempts: int = POST_RETRY_LIMIT,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            last_error = exc
        else:
            if response.status_code >= 500 or response.status_code == 429:
                if attempt == max_attempts:
                    response.raise_for_status()
                else:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
            response.raise_for_status()
            return response

        if attempt < max_attempts:
            time.sleep(RETRY_BACKOFF_SECONDS)

    if last_error:
        raise last_error
    raise RuntimeError("Failed to POST after retries")


def wait_for_health(client: httpx.Client, base_url: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = client.get(f"{base_url}/health")
            response.raise_for_status()
            return
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    if last_error:
        raise last_error
    raise TimeoutError("Timed out waiting for /health")


def resolve_source_id(
    client: httpx.Client, base_url: str, pdf_path: Path, timeout_s: int
) -> tuple[str, dict[str, Any]]:
    sources = list_sources(client, base_url)
    ready = next(
        (
            item
            for item in sources
            if item.get("original_filename") == pdf_path.name
            and item.get("status") == "READY"
        ),
        None,
    )
    if ready:
        return str(ready["id"]), ready
    existing = find_source_by_filename(sources, pdf_path.name)
    if existing and existing.get("status") == "READY":
        return str(existing["id"]), existing

    if existing and existing.get("status") in {"UPLOADED", "PROCESSING"}:
        ready = wait_for_source(client, base_url, str(existing["id"]), timeout_s=timeout_s)
        return str(ready["id"]), ready
    if existing and existing.get("status") == "FAILED":
        delete_source(client, base_url, str(existing["id"]))

    payload = upload_source(client, base_url, pdf_path, title=f"Eval Fixture: {pdf_path.name}")
    try:
        ready = wait_for_source(client, base_url, str(payload["id"]), timeout_s=timeout_s)
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc} (check worker logs and OPENAI_API_KEY/AI_PROVIDER settings)"
        ) from exc
    return str(ready["id"]), ready


def validate_snippet_integrity(
    item: dict[str, Any],
    chunk_info: dict[str, Any],
    label: str,
    counts: dict[str, int],
    failures: list[str],
) -> None:
    snippet_text = item.get("snippet")
    snippet_start = item.get("snippet_start")
    snippet_end = item.get("snippet_end")
    absolute_start = item.get("absolute_start")
    absolute_end = item.get("absolute_end")

    chunk_text = chunk_info.get("text")
    if not isinstance(chunk_text, str):
        failures.append(f"{label}_chunk_text_missing")
        return

    if snippet_start is None or snippet_end is None:
        return

    if not isinstance(snippet_start, int) or not isinstance(snippet_end, int):
        counts["snippet_oob_count"] += 1
        failures.append(f"{label}_snippet_bounds_invalid")
        return

    if snippet_start < 0 or snippet_end <= snippet_start or snippet_end > len(chunk_text):
        counts["snippet_oob_count"] += 1
        failures.append(f"{label}_snippet_oob")
        return

    expected_slice = chunk_text[snippet_start:snippet_end]
    if not isinstance(snippet_text, str) or snippet_text != expected_slice:
        counts["snippet_slice_mismatch_count"] += 1
        failures.append(f"{label}_snippet_slice_mismatch")

    char_start = chunk_info.get("char_start")
    char_end = chunk_info.get("char_end")
    if char_start is None:
        if absolute_start is not None or absolute_end is not None:
            counts["absolute_mismatch_count"] += 1
            failures.append(f"{label}_absolute_present_without_char_start")
        return

    if not isinstance(char_start, int):
        failures.append(f"{label}_char_start_invalid")
        return

    if absolute_start is None or absolute_end is None:
        counts["absolute_missing_count"] += 1
        failures.append(f"{label}_absolute_missing")
        return

    if not isinstance(absolute_start, int) or not isinstance(absolute_end, int):
        counts["absolute_mismatch_count"] += 1
        failures.append(f"{label}_absolute_invalid")
        return

    if absolute_end <= absolute_start:
        counts["absolute_oob_count"] += 1
        failures.append(f"{label}_absolute_bounds_invalid")
        return

    expected_abs_start = char_start + snippet_start
    expected_abs_end = char_start + snippet_end
    if absolute_start != expected_abs_start or absolute_end != expected_abs_end:
        counts["absolute_mismatch_count"] += 1
        failures.append(f"{label}_absolute_mismatch")

    if isinstance(char_end, int) and absolute_end > char_end:
        counts["absolute_oob_count"] += 1
        failures.append(f"{label}_absolute_exceeds_char_end")
    if absolute_end > char_start + len(chunk_text):
        counts["absolute_oob_count"] += 1
        failures.append(f"{label}_absolute_exceeds_text_length")


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
    chunk_cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int]]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()

    response = post_with_retries(
        client,
        f"{base_url}/query/verified/highlights",
        {"question": question, "source_ids": [source_id]},
    )
    payload = cast(dict[str, Any], response.json())

    failures: list[str] = []
    counts = {
        "invalid_citation_count": 0,
        "invalid_evidence_id_count": 0,
        "snippet_slice_mismatch_count": 0,
        "snippet_oob_count": 0,
        "absolute_mismatch_count": 0,
        "absolute_oob_count": 0,
        "absolute_missing_count": 0,
    }

    answer_raw = payload.get("answer")
    answer = str(answer_raw).strip() if isinstance(answer_raw, str) else ""

    if expected == "INSUFFICIENT_EVIDENCE":
        if not answer or not contains_insufficient_evidence(answer):
            failures.append("missing_insufficient_evidence_marker")
        result = {
            "id": case.get("id"),
            "question": question,
            "expected_behavior": expected,
            "answer": answer,
            "passed": not failures,
            "failures": failures,
        }
        return result, counts

    if expected != "ANSWERABLE":
        failures.append(f"unknown_expected_behavior({expected})")

    citations_raw = payload.get("citations")
    claims_raw = payload.get("claims")

    citations = citations_raw if isinstance(citations_raw, list) else []
    claims = claims_raw if isinstance(claims_raw, list) else []

    for citation in citations:
        if not isinstance(citation, dict):
            counts["invalid_citation_count"] += 1
            failures.append("invalid_citation_shape")
            continue
        chunk_id = citation.get("chunk_id")
        source = citation.get("source_id")
        citation_valid = True
        if not chunk_id or str(chunk_id) not in valid_chunk_ids:
            citation_valid = False
            failures.append(f"invalid_chunk_id({chunk_id})")
        if not source or str(source) != str(source_id):
            citation_valid = False
            failures.append(f"invalid_source_id({source})")
        if not citation_valid:
            counts["invalid_citation_count"] += 1
            continue

        chunk_id_str = str(chunk_id)
        chunk_info = chunk_cache.get(chunk_id_str)
        if chunk_info is None:
            chunk_info = get_debug_chunk_info(client, base_url, chunk_id_str)
            chunk_cache[chunk_id_str] = chunk_info
        validate_snippet_integrity(citation, chunk_info, "citation", counts, failures)

    for claim_idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            failures.append(f"invalid_claim_shape(index={claim_idx})")
            continue
        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            failures.append(f"invalid_evidence_list(index={claim_idx})")
            continue

        for ev_idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                counts["invalid_evidence_id_count"] += 1
                failures.append(f"invalid_evidence_shape(index={claim_idx}, evidence={ev_idx})")
                continue
            chunk_id = ev.get("chunk_id")
            if not chunk_id or str(chunk_id) not in valid_chunk_ids:
                counts["invalid_evidence_id_count"] += 1
                failures.append(
                    "invalid_evidence_chunk_id("
                    f"index={claim_idx}, evidence={ev_idx}, id={chunk_id})"
                )
                continue

            chunk_id_str = str(chunk_id)
            chunk_info = chunk_cache.get(chunk_id_str)
            if chunk_info is None:
                chunk_info = get_debug_chunk_info(client, base_url, chunk_id_str)
                chunk_cache[chunk_id_str] = chunk_info
            label = f"evidence_{claim_idx}_{ev_idx}"
            validate_snippet_integrity(ev, chunk_info, label, counts, failures)

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "answer": answer,
        "invalid_citation_count": counts["invalid_citation_count"],
        "invalid_evidence_id_count": counts["invalid_evidence_id_count"],
        "snippet_slice_mismatch_count": counts["snippet_slice_mismatch_count"],
        "snippet_oob_count": counts["snippet_oob_count"],
        "absolute_mismatch_count": counts["absolute_mismatch_count"],
        "absolute_oob_count": counts["absolute_oob_count"],
        "absolute_missing_count": counts["absolute_missing_count"],
        "passed": not failures,
        "failures": failures,
    }
    return result, counts


def write_report(
    output_path: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    failed = [case for case in results if not case.get("passed")]

    lines: list[str] = ["# Evidence Integrity Report", ""]
    lines.append(f"- Timestamp: {metadata.get('timestamp')}")
    lines.append(f"- Base URL: {metadata.get('base_url')}")
    lines.append(f"- Source ID: {metadata.get('source_id')}")
    lines.append(f"- Dataset: {metadata.get('dataset')}")
    lines.append(f"- Git commit: {metadata.get('git_commit') or 'unknown'}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Total cases: {metrics.get('total_cases', 0)}")
    lines.append(f"- Passed: {metrics.get('passed_cases', 0)}")
    lines.append(f"- Failed: {metrics.get('failed_cases', 0)}")
    lines.append("")
    lines.append("## Metrics")
    lines.append(f"- invalid_citation_count: {metrics.get('invalid_citation_count', 0)}")
    lines.append(
        f"- invalid_evidence_id_count: {metrics.get('invalid_evidence_id_count', 0)}"
    )
    lines.append(
        f"- snippet_slice_mismatch_count: {metrics.get('snippet_slice_mismatch_count', 0)}"
    )
    lines.append(f"- snippet_oob_count: {metrics.get('snippet_oob_count', 0)}")
    lines.append(f"- absolute_mismatch_count: {metrics.get('absolute_mismatch_count', 0)}")
    lines.append(f"- absolute_oob_count: {metrics.get('absolute_oob_count', 0)}")
    lines.append(f"- absolute_missing_count: {metrics.get('absolute_missing_count', 0)}")
    lines.append("")

    if failed:
        lines.append("## Failed Cases")
        for case in failed:
            lines.append(
                f"- {case.get('id')}: {', '.join(case.get('failures', []))}"
            )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evidence integrity eval.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--fixture", default=None)
    args = parser.parse_args()

    cases, fixture_name = load_dataset(args.dataset)
    if args.fixture:
        fixture_name = args.fixture

    base_url = get_base_url(args.base_url)
    ready_timeout = get_env_int("EVAL_READY_TIMEOUT_SECONDS", DEFAULT_READY_TIMEOUT_SECONDS)
    http_timeout = get_env_int("EVAL_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    require_openai_env()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=http_timeout) as client:
        wait_for_health(client, base_url, timeout_s=ready_timeout)
        pdf_path = resolve_fixture_path(fixture_name)
        source_id, _ = resolve_source_id(client, base_url, pdf_path, ready_timeout)
        valid_chunk_ids = set(get_debug_chunk_ids(client, base_url, source_id))

        results: list[dict[str, Any]] = []
        metrics = {
            "total_cases": len(cases),
            "passed_cases": 0,
            "failed_cases": 0,
            "invalid_citation_count": 0,
            "invalid_evidence_id_count": 0,
            "snippet_slice_mismatch_count": 0,
            "snippet_oob_count": 0,
            "absolute_mismatch_count": 0,
            "absolute_oob_count": 0,
            "absolute_missing_count": 0,
        }
        chunk_cache: dict[str, dict[str, Any]] = {}

        for case in cases:
            result, counts = evaluate_case(
                case, client, base_url, source_id, valid_chunk_ids, chunk_cache
            )
            results.append(result)
            metrics["invalid_citation_count"] += counts["invalid_citation_count"]
            metrics["invalid_evidence_id_count"] += counts["invalid_evidence_id_count"]
            metrics["snippet_slice_mismatch_count"] += counts[
                "snippet_slice_mismatch_count"
            ]
            metrics["snippet_oob_count"] += counts["snippet_oob_count"]
            metrics["absolute_mismatch_count"] += counts["absolute_mismatch_count"]
            metrics["absolute_oob_count"] += counts["absolute_oob_count"]
            metrics["absolute_missing_count"] += counts["absolute_missing_count"]

        metrics["passed_cases"] = sum(1 for case in results if case.get("passed"))
        metrics["failed_cases"] = metrics["total_cases"] - metrics["passed_cases"]

    metadata = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "git_commit": get_git_commit(),
        "base_url": base_url,
        "source_id": source_id,
        "dataset": str(args.dataset),
    }

    json_path = OUT_DIR / "eval_evidence_integrity_results.json"
    report_path = OUT_DIR / "eval_evidence_integrity_report.md"
    json_path.write_text(
        json.dumps({"metadata": metadata, "metrics": metrics, "cases": results}, indent=2),
        encoding="utf-8",
    )
    write_report(report_path, results, metrics, metadata)

    if metrics["failed_cases"]:
        print(
            "Evidence integrity eval complete: "
            f"{metrics['passed_cases']}/{metrics['total_cases']} passed"
        )
        print(f"Results: {json_path}")
        print(f"Report: {report_path}")
        sys.exit(1)

    print(
        "Evidence integrity eval complete: "
        f"{metrics['passed_cases']}/{metrics['total_cases']} passed"
    )
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
