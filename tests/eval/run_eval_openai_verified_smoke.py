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
    get_debug_chunk_text,
    list_sources,
    upload_source,
    wait_for_source,
)

DATASET_PATH = Path(__file__).resolve().parent / "golden_openai_verified_smoke.json"
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
CONTRADICTION_PREFIX_MARKER = "contradictions detected in the source material"
ALLOWED_RELATIONS = {"SUPPORTS", "CONTRADICTS", "RELATED"}
ANSWER_STYLES = {"ORIGINAL", "CONFLICT_REWRITTEN", "INSUFFICIENT_EVIDENCE"}
OVERALL_VERDICTS = {"OK", "HAS_CONTRADICTIONS", "INSUFFICIENT_EVIDENCE"}
VERDICT_KEYS = (
    "SUPPORTED",
    "WEAK_SUPPORT",
    "UNSUPPORTED",
    "CONTRADICTED",
    "CONFLICTING",
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
        raise RuntimeError(
            "AI_PROVIDER must be set to openai for eval-openai-verified-smoke"
        )
    api_key = get_env_value("OPENAI_API_KEY") or ""
    if not api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is required for eval-openai-verified-smoke")


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


def compute_verdict_counts(claims: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in VERDICT_KEYS}
    for claim in claims:
        verdict = str(claim.get("verdict", "")).strip().upper()
        if verdict in counts:
            counts[verdict] += 1
    return counts


def validate_summary(
    *,
    answer: str,
    answer_style: str,
    claims: list[dict[str, Any]],
    citations_count: int,
    summary_payload: dict[str, Any] | None,
    failures: list[str],
) -> bool:
    if not isinstance(summary_payload, dict):
        failures.append("missing_verification_summary")
        return False

    summary_answer_style = summary_payload.get("answer_style")
    if not isinstance(summary_answer_style, str) or not summary_answer_style.strip():
        failures.append("missing_summary_answer_style")
        summary_answer_style = None
    else:
        summary_answer_style = summary_answer_style.strip().upper()

    if answer_style not in ANSWER_STYLES:
        failures.append("invalid_answer_style")
    if summary_answer_style and summary_answer_style != answer_style:
        failures.append("answer_style_mismatch")

    counts = compute_verdict_counts(claims)
    summary_counts_raw = {
        "SUPPORTED": summary_payload.get("supported_count"),
        "WEAK_SUPPORT": summary_payload.get("weak_support_count"),
        "UNSUPPORTED": summary_payload.get("unsupported_count"),
        "CONTRADICTED": summary_payload.get("contradicted_count"),
        "CONFLICTING": summary_payload.get("conflicting_count"),
    }
    summary_counts: dict[str, int] = {}
    for verdict_key, value in summary_counts_raw.items():
        if not isinstance(value, int):
            failures.append(f"invalid_summary_count(verdict={verdict_key})")
        else:
            summary_counts[verdict_key] = value
    if len(summary_counts) == len(summary_counts_raw):
        for verdict_key, count in summary_counts.items():
            expected_count = counts.get(verdict_key, 0)
            if count != expected_count:
                failures.append(
                    "summary_count_mismatch("
                    f"verdict={verdict_key}, expected={expected_count}, got={count})"
                )

    summary_has_contradictions = summary_payload.get("has_contradictions")
    if not isinstance(summary_has_contradictions, bool):
        failures.append("invalid_summary_has_contradictions")
        summary_has_contradictions = None

    summary_overall = str(summary_payload.get("overall_verdict", "")).strip().upper()
    if summary_overall not in OVERALL_VERDICTS:
        failures.append("invalid_summary_overall_verdict")

    expected_has_contradictions = (
        counts.get("CONTRADICTED", 0) + counts.get("CONFLICTING", 0)
    ) > 0
    if summary_has_contradictions is not None:
        if summary_has_contradictions != expected_has_contradictions:
            failures.append("summary_has_contradictions_mismatch")

    claims_count = len(claims)
    all_unsupported = claims_count > 0 and counts.get("UNSUPPORTED", 0) == claims_count
    insufficient_expected = contains_insufficient_evidence(answer) or (
        citations_count == 0 and all_unsupported
    )
    if insufficient_expected:
        expected_overall = "INSUFFICIENT_EVIDENCE"
    elif expected_has_contradictions:
        expected_overall = "HAS_CONTRADICTIONS"
    else:
        expected_overall = "OK"
    if summary_overall in OVERALL_VERDICTS and summary_overall != expected_overall:
        failures.append(
            "summary_overall_verdict_mismatch("
            f"expected={expected_overall}, got={summary_overall})"
        )

    if answer.lower().startswith(CONTRADICTION_PREFIX_MARKER):
        expected_style = "CONFLICT_REWRITTEN"
    elif expected_overall == "INSUFFICIENT_EVIDENCE":
        expected_style = "INSUFFICIENT_EVIDENCE"
    else:
        expected_style = "ORIGINAL"
    if summary_answer_style and summary_answer_style != expected_style:
        failures.append(
            "summary_answer_style_mismatch("
            f"expected={expected_style}, got={summary_answer_style})"
        )
    if answer_style and answer_style in ANSWER_STYLES and answer_style != expected_style:
        failures.append(
            "answer_style_mismatch_expected("
            f"expected={expected_style}, got={answer_style})"
        )

    summary_failure_tags = {
        "missing_summary_answer_style",
        "missing_verification_summary",
        "answer_style_mismatch",
        "answer_style_mismatch_expected",
        "invalid_answer_style",
    }
    return not any(
        failure.startswith("summary_") or failure in summary_failure_tags
        for failure in failures
    )


def validate_highlights(
    claims: list[dict[str, Any]],
    valid_chunk_ids: set[str],
    chunk_text_cache: dict[str, str],
    client: httpx.Client,
    base_url: str,
    counts: dict[str, int],
    failures: list[str],
) -> None:
    for claim_idx, claim in enumerate(claims):
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
            relation = str(ev.get("relation", "")).strip().upper()
            if relation not in ALLOWED_RELATIONS:
                counts["invalid_evidence_id_count"] += 1
                failures.append(
                    "invalid_evidence_relation("
                    f"index={claim_idx}, evidence={ev_idx}, relation={relation})"
                )
                continue

            highlight_start = ev.get("highlight_start")
            highlight_end = ev.get("highlight_end")
            highlight_text = ev.get("highlight_text")
            if highlight_start is None or highlight_end is None or highlight_text is None:
                counts["highlight_null_count"] += 1
                continue
            if not isinstance(highlight_start, int) or not isinstance(highlight_end, int):
                counts["highlight_oob_count"] += 1
                failures.append(
                    "highlight_bounds_invalid"
                    f"(index={claim_idx}, evidence={ev_idx})"
                )
                continue
            if highlight_start < 0 or highlight_end <= highlight_start:
                counts["highlight_oob_count"] += 1
                failures.append(
                    "highlight_bounds_invalid"
                    f"(index={claim_idx}, evidence={ev_idx})"
                )
                continue

            chunk_id_str = str(chunk_id)
            chunk_text = chunk_text_cache.get(chunk_id_str)
            if chunk_text is None:
                chunk_text = get_debug_chunk_text(client, base_url, chunk_id_str)
                chunk_text_cache[chunk_id_str] = chunk_text

            if highlight_end > len(chunk_text):
                counts["highlight_oob_count"] += 1
                failures.append(
                    "highlight_oob"
                    f"(index={claim_idx}, evidence={ev_idx}, end={highlight_end})"
                )
                continue

            expected_slice = chunk_text[highlight_start:highlight_end]
            if highlight_text != expected_slice:
                counts["highlight_slice_mismatch_count"] += 1
                failures.append(
                    "highlight_slice_mismatch"
                    f"(index={claim_idx}, evidence={ev_idx})"
                )


def evaluate_endpoint(
    *,
    endpoint: str,
    expected: str,
    question: str,
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
    chunk_text_cache: dict[str, str],
    check_highlights: bool,
) -> tuple[list[str], dict[str, int], bool]:
    failures: list[str] = []
    counts = {
        "invalid_citation_count": 0,
        "invalid_evidence_id_count": 0,
        "highlight_slice_mismatch_count": 0,
        "highlight_oob_count": 0,
        "highlight_null_count": 0,
        "summary_consistency_failures": 0,
    }

    response = post_with_retries(
        client,
        f"{base_url}{endpoint}",
        {"question": question, "source_ids": [source_id]},
    )
    payload = cast(dict[str, Any], response.json())

    answer_raw = payload.get("answer")
    answer = str(answer_raw).strip() if isinstance(answer_raw, str) else ""
    answer_style = str(payload.get("answer_style", "")).strip().upper()

    citations_raw = payload.get("citations")
    claims_raw = payload.get("claims")
    summary_payload = payload.get("verification_summary")

    if not isinstance(answer_raw, str):
        failures.append("invalid_answer_type")

    if not isinstance(citations_raw, list):
        failures.append("citations_not_list")
        citations = []
    else:
        citations = citations_raw
    citations_count = len(citations)

    if not isinstance(claims_raw, list):
        failures.append("claims_not_list")
        claims = []
    else:
        claims = [claim for claim in claims_raw if isinstance(claim, dict)]
        if len(claims) != len(claims_raw):
            failures.append("invalid_claim_shape")

    if expected == "ANSWERABLE":
        if not answer:
            failures.append("empty_answer")
        if citations_count == 0:
            failures.append("missing_citations")
        if not claims:
            failures.append("missing_claims")
    elif expected == "INSUFFICIENT_EVIDENCE":
        if not answer or not contains_insufficient_evidence(answer):
            failures.append("missing_insufficient_evidence_marker")
    else:
        failures.append(f"unknown_expected_behavior({expected})")

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

    for claim_idx, claim in enumerate(claims):
        for key in ("verdict", "support_score", "contradiction_score", "evidence"):
            if key not in claim:
                failures.append(f"missing_claim_field(index={claim_idx}, field={key})")

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

    summary_consistent = validate_summary(
        answer=answer,
        answer_style=answer_style,
        claims=claims,
        citations_count=citations_count,
        summary_payload=summary_payload if isinstance(summary_payload, dict) else None,
        failures=failures,
    )
    if not summary_consistent:
        counts["summary_consistency_failures"] += 1

    if check_highlights:
        validate_highlights(
            claims,
            valid_chunk_ids,
            chunk_text_cache,
            client,
            base_url,
            counts,
            failures,
        )

    return failures, counts, summary_consistent


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
    chunk_text_cache: dict[str, str],
) -> tuple[dict[str, Any], dict[str, int]]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()

    all_failures: list[str] = []
    counts_total = {
        "invalid_citation_count": 0,
        "invalid_evidence_id_count": 0,
        "highlight_slice_mismatch_count": 0,
        "highlight_oob_count": 0,
        "highlight_null_count": 0,
        "summary_consistency_failures": 0,
        "summary_consistency_passed": 0,
    }

    for label, endpoint, check_highlights in (
        ("verified", "/query/verified", False),
        ("highlights", "/query/verified/highlights", True),
    ):
        failures, counts, summary_consistent = evaluate_endpoint(
            endpoint=endpoint,
            expected=expected,
            question=question,
            client=client,
            base_url=base_url,
            source_id=source_id,
            valid_chunk_ids=valid_chunk_ids,
            chunk_text_cache=chunk_text_cache,
            check_highlights=check_highlights,
        )
        if summary_consistent:
            counts_total["summary_consistency_passed"] += 1
        for key in (
            "invalid_citation_count",
            "invalid_evidence_id_count",
            "highlight_slice_mismatch_count",
            "highlight_oob_count",
            "highlight_null_count",
            "summary_consistency_failures",
        ):
            counts_total[key] += counts[key]
        for failure in failures:
            all_failures.append(f"{label}:{failure}")

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "passed": not all_failures,
        "failures": all_failures,
    }
    return result, counts_total


def write_report(
    output_path: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    failed = [case for case in results if not case.get("passed")]

    lines: list[str] = ["# OpenAI Verified Smoke Report", ""]
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
        f"- highlight_slice_mismatch_count: {metrics.get('highlight_slice_mismatch_count', 0)}"
    )
    lines.append(f"- highlight_oob_count: {metrics.get('highlight_oob_count', 0)}")
    lines.append(f"- highlight_null_count: {metrics.get('highlight_null_count', 0)}")
    lines.append(
        f"- summary_consistency_rate: {metrics.get('summary_consistency_rate', 0.0)}"
    )
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
    parser = argparse.ArgumentParser(description="Run OpenAI verified smoke eval.")
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
            "highlight_slice_mismatch_count": 0,
            "highlight_oob_count": 0,
            "highlight_null_count": 0,
            "summary_consistency_rate": 0.0,
            "summary_consistency_passed": 0,
            "summary_consistency_failures": 0,
        }
        chunk_text_cache: dict[str, str] = {}

        for case in cases:
            result, counts = evaluate_case(
                case, client, base_url, source_id, valid_chunk_ids, chunk_text_cache
            )
            results.append(result)
            metrics["invalid_citation_count"] += counts["invalid_citation_count"]
            metrics["invalid_evidence_id_count"] += counts["invalid_evidence_id_count"]
            metrics["highlight_slice_mismatch_count"] += counts[
                "highlight_slice_mismatch_count"
            ]
            metrics["highlight_oob_count"] += counts["highlight_oob_count"]
            metrics["highlight_null_count"] += counts["highlight_null_count"]
            metrics["summary_consistency_passed"] += counts["summary_consistency_passed"]
            metrics["summary_consistency_failures"] += counts[
                "summary_consistency_failures"
            ]

        metrics["passed_cases"] = sum(1 for case in results if case.get("passed"))
        metrics["failed_cases"] = metrics["total_cases"] - metrics["passed_cases"]
        total_checks = len(cases) * 2
        if total_checks:
            metrics["summary_consistency_rate"] = round(
                metrics["summary_consistency_passed"] / total_checks, 4
            )

    metadata = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "git_commit": get_git_commit(),
        "base_url": base_url,
        "source_id": source_id,
        "dataset": str(args.dataset),
    }

    json_path = OUT_DIR / "eval_openai_verified_smoke_results.json"
    report_path = OUT_DIR / "eval_openai_verified_smoke_report.md"
    json_path.write_text(
        json.dumps({"metadata": metadata, "metrics": metrics, "cases": results}, indent=2),
        encoding="utf-8",
    )
    write_report(report_path, results, metrics, metadata)

    if metrics["failed_cases"]:
        print(
            "OpenAI verified smoke eval complete: "
            f"{metrics['passed_cases']}/{metrics['total_cases']} passed"
        )
        print(f"Results: {json_path}")
        print(f"Report: {report_path}")
        sys.exit(1)

    print(
        "OpenAI verified smoke eval complete: "
        f"{metrics['passed_cases']}/{metrics['total_cases']} passed"
    )
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
