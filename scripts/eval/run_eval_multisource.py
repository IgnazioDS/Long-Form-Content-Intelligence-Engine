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

sys.path.append(str(Path(__file__).resolve().parents[1]))

from _common.api_client import (  # noqa: E402
    find_source_by_filename,
    get_base_url,
    get_debug_chunk_ids,
    list_sources,
    upload_source,
    wait_for_source,
)

DATASET_PATH = Path(__file__).resolve().parent / "golden_multisource.json"
OUT_DIR = Path(__file__).resolve().parent / "out"
THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"
DEFAULT_READY_TIMEOUT_SECONDS = 60
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
POST_RETRY_LIMIT = 3
RETRY_BACKOFF_SECONDS = 0.5

EVAL_MULTISOURCE_GATE_DEFINITIONS = (
    ("invalid_citation_count_max", "invalid_citation_count", "<="),
    ("answerable_pass_rate_min", "answerable_pass_rate", ">="),
    ("multi_source_pass_rate_min", "multi_source_pass_rate", ">="),
    ("avg_citations_per_answerable_min", "avg_citations_per_answerable", ">="),
)

GENERIC_ANSWER_PHRASES = (
    "insufficient evidence",
    "based on the provided context",
    "suggested follow-ups",
    "clarify the question",
    "ask for a narrower question",
)

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


def load_dataset(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Eval dataset must be a JSON object")
    fixtures = payload.get("fixtures", [])
    cases_payload = payload.get("cases", [])
    if not isinstance(fixtures, list) or not all(
        isinstance(item, str) for item in fixtures
    ):
        raise ValueError("fixtures must be a list of strings")
    if not isinstance(cases_payload, list):
        raise ValueError("cases must be a list")

    cases: list[dict[str, Any]] = []
    for item in cases_payload:
        if not isinstance(item, dict):
            raise ValueError("Each eval case must be an object")
        for key in ("id", "question", "expected_behavior"):
            if key not in item:
                raise ValueError(f"Missing required field: {key}")
        cases.append(item)
    return cases, [fixture.strip() for fixture in fixtures if fixture.strip()]


def load_thresholds(path: Path, section: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Thresholds file must be a JSON object")
    thresholds = payload.get(section)
    if not isinstance(thresholds, dict):
        raise ValueError(f"Missing thresholds section: {section}")
    return thresholds


def evaluate_quality_gates(
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    definitions: tuple[tuple[str, str, str], ...],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for gate_name, metric_key, operator in definitions:
        if gate_name not in thresholds:
            raise ValueError(f"Missing threshold: {gate_name}")
        threshold_value = thresholds[gate_name]
        actual_value = metrics.get(metric_key)
        passed = False
        if actual_value is not None:
            try:
                actual_numeric = float(actual_value)
                threshold_numeric = float(threshold_value)
            except (TypeError, ValueError):
                passed = False
            else:
                if operator == "<=":
                    passed = actual_numeric <= threshold_numeric
                elif operator == ">=":
                    passed = actual_numeric >= threshold_numeric
                else:
                    raise ValueError(f"Unsupported operator: {operator}")

        results[gate_name] = {
            "passed": passed,
            "expected": f"{operator} {threshold_value}",
            "actual": actual_value,
        }
    return results


def get_git_commit() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.strip() or None


def is_generic_answer(answer: str) -> bool:
    lowered = answer.strip().lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in GENERIC_ANSWER_PHRASES)


def contains_insufficient_evidence(answer: str) -> bool:
    lowered = answer.strip().lower()
    return any(phrase in lowered for phrase in INSUFFICIENT_EVIDENCE_PHRASES)


def resolve_fixture_paths(fixtures: list[str]) -> list[Path]:
    base_dir = Path(__file__).resolve().parents[1] / "fixtures"
    paths: list[Path] = []
    for name in fixtures:
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = base_dir / name
        if not candidate.exists():
            raise FileNotFoundError(f"Fixture not found: {candidate}")
        paths.append(candidate)
    return paths


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


def resolve_source_id(
    client: httpx.Client,
    base_url: str,
    pdf_path: Path,
    timeout_s: int,
) -> tuple[str, dict[str, Any]]:
    sources = list_sources(client, base_url)
    existing = find_source_by_filename(sources, pdf_path.name)
    if existing and existing.get("status") == "READY":
        return str(existing["id"]), existing

    if existing and existing.get("status") in {"UPLOADED", "PROCESSING"}:
        ready = wait_for_source(client, base_url, str(existing["id"]), timeout_s=timeout_s)
        return str(ready["id"]), ready

    payload = upload_source(client, base_url, pdf_path, title=f"Eval Fixture: {pdf_path.name}")
    ready = wait_for_source(client, base_url, str(payload["id"]), timeout_s=timeout_s)
    return str(ready["id"]), ready


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_ids: list[str],
    chunk_sources: dict[str, str],
    required_sources: dict[str, str],
) -> tuple[dict[str, Any], int, bool]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()
    min_citations = case.get("min_citations")
    if min_citations is None:
        min_citations = 1 if expected == "ANSWERABLE" else 0
    try:
        min_citations = int(min_citations)
    except (TypeError, ValueError):
        min_citations = 1 if expected == "ANSWERABLE" else 0

    query_payload = {"question": question, "source_ids": source_ids}
    response = post_with_retries(client, f"{base_url}/query/grouped", query_payload)
    payload = cast(dict[str, Any], response.json())

    answer = str(payload.get("answer", "")).strip()
    citations = payload.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    citations_count = len(citations)

    failures: list[str] = []

    if expected == "ANSWERABLE":
        if not answer:
            failures.append("empty_answer")
        if is_generic_answer(answer):
            failures.append("generic_answer")
        if citations_count < min_citations:
            failures.append(
                f"insufficient_citations(expected>={min_citations}, got={citations_count})"
            )
    elif expected == "INSUFFICIENT_EVIDENCE":
        if not contains_insufficient_evidence(answer):
            failures.append("missing_insufficient_evidence_marker")
    else:
        failures.append(f"unknown_expected_behavior({expected})")

    invalid_citations = 0
    citation_source_ids: list[str] = []
    for citation in citations:
        citation_valid = True
        if not isinstance(citation, dict):
            citation_valid = False
            failures.append("invalid_citation_shape")
        else:
            chunk_id = citation.get("chunk_id")
            citation_source = citation.get("source_id")
            chunk_source = chunk_sources.get(str(chunk_id))
            if not chunk_source:
                citation_valid = False
                failures.append(f"invalid_chunk_id({chunk_id})")
            if not citation_source or str(citation_source) != str(chunk_source):
                citation_valid = False
                failures.append(f"invalid_source_id({citation_source})")
            if chunk_source:
                citation_source_ids.append(str(chunk_source))
        if not citation_valid:
            invalid_citations += 1

    required_source_ids = {
        required_sources[name] for name in case.get("require_sources", [])
        if name in required_sources
    }
    multi_source_passed = True
    if required_source_ids:
        missing_sources = [
            source_id for source_id in required_source_ids if source_id not in citation_source_ids
        ]
        if missing_sources:
            multi_source_passed = False
            failures.append("missing_required_sources")

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "answer": answer,
        "citations": citations,
        "citations_count": citations_count,
        "min_citations": min_citations,
        "required_sources": case.get("require_sources", []),
        "passed": not failures,
        "failures": failures,
    }
    return result, invalid_citations, multi_source_passed


def write_report(
    output_path: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
    quality_gates: dict[str, dict[str, Any]],
) -> None:
    failed = [case for case in results if not case.get("passed")]

    lines: list[str] = ["# Evaluation Report", ""]
    lines.append(f"- Timestamp: {metadata.get('timestamp')}")
    lines.append(f"- Base URL: {metadata.get('base_url')}")
    lines.append(f"- Source IDs: {', '.join(metadata.get('source_ids', []))}")
    lines.append(f"- Fixtures: {', '.join(metadata.get('fixtures', []))}")
    lines.append(f"- Dataset: {metadata.get('dataset')}")
    lines.append(f"- Git commit: {metadata.get('git_commit') or 'unknown'}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Total cases: {metrics['total_cases']}")
    lines.append(f"- Passed cases: {metrics['passed_cases']}")
    lines.append(f"- Failed cases: {metrics['failed_cases']}")
    lines.append("")
    lines.append("## Metrics")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for key in (
        "answerable_pass_rate",
        "multi_source_pass_rate",
        "avg_citations_per_answerable",
        "invalid_citation_count",
    ):
        lines.append(f"| {key} | {metrics[key]} |")
    lines.append("")
    lines.append("## Quality Gates")
    if not quality_gates:
        lines.append("- No gates configured.")
    else:
        lines.append("| Gate | Status | Actual | Expected |")
        lines.append("| --- | --- | --- | --- |")
        for gate_name, gate in quality_gates.items():
            status = "PASS" if gate.get("passed") else "FAIL"
            lines.append(
                f"| {gate_name} | {status} | {gate.get('actual')} | {gate.get('expected')} |"
            )
    lines.append("")
    lines.append("## Failures")
    if not failed:
        lines.append("- All cases passed.")
    else:
        for case in failed:
            failures = ", ".join(case.get("failures", []))
            lines.append(f"- {case.get('id')}: {case.get('question')} -> {failures}")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-source evaluation harness.")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--dataset", default=str(DATASET_PATH), help="Path to dataset JSON")
    parser.add_argument(
        "--thresholds",
        default=str(THRESHOLDS_PATH),
        help="Path to thresholds JSON",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    thresholds_path = Path(args.thresholds)
    if not thresholds_path.exists():
        raise FileNotFoundError(f"Thresholds not found: {thresholds_path}")

    ready_timeout = get_env_int(
        "EVAL_READY_TIMEOUT_SECONDS", DEFAULT_READY_TIMEOUT_SECONDS
    )
    http_timeout = get_env_int("EVAL_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)

    base_url = get_base_url(args.base_url)
    cases, fixture_names = load_dataset(dataset_path)
    fixture_paths = resolve_fixture_paths(fixture_names)
    thresholds = load_thresholds(thresholds_path, "eval_multisource")

    with httpx.Client(timeout=float(http_timeout)) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()

        source_ids: list[str] = []
        source_lookup: dict[str, str] = {}
        chunk_sources: dict[str, str] = {}
        for fixture_path in fixture_paths:
            source_id, source_payload = resolve_source_id(
                client, base_url, fixture_path, timeout_s=ready_timeout
            )
            if source_payload.get("status") != "READY":
                raise RuntimeError("Source did not reach READY status")
            source_ids.append(source_id)
            source_lookup[fixture_path.name] = source_id

            valid_chunk_ids = set(get_debug_chunk_ids(client, base_url, source_id))
            if not valid_chunk_ids:
                raise RuntimeError("No chunks available for citation validation")
            for chunk_id in valid_chunk_ids:
                chunk_sources[str(chunk_id)] = source_id

        results: list[dict[str, Any]] = []
        invalid_citation_count = 0
        answerable_cases = 0
        answerable_passed = 0
        citation_total_for_answerable = 0
        multi_source_cases = 0
        multi_source_passed = 0

        for case in cases:
            result, invalid_count, multi_source_ok = evaluate_case(
                case, client, base_url, source_ids, chunk_sources, source_lookup
            )
            results.append(result)
            invalid_citation_count += invalid_count

            expected = result.get("expected_behavior")
            if expected == "ANSWERABLE":
                answerable_cases += 1
                citation_total_for_answerable += int(result.get("citations_count", 0))
                if result.get("passed"):
                    answerable_passed += 1

            required_sources = case.get("require_sources", [])
            if isinstance(required_sources, list) and required_sources:
                multi_source_cases += 1
                if multi_source_ok:
                    multi_source_passed += 1

    total_cases = len(results)
    passed_cases = sum(1 for case in results if case.get("passed"))
    failed_cases = total_cases - passed_cases

    answerable_pass_rate = (
        round(answerable_passed / answerable_cases, 4) if answerable_cases else 1.0
    )
    avg_citations_answerable = (
        round(citation_total_for_answerable / answerable_cases, 4)
        if answerable_cases
        else 0.0
    )
    multi_source_pass_rate = (
        round(multi_source_passed / multi_source_cases, 4) if multi_source_cases else 1.0
    )

    timestamp = datetime.now(UTC).isoformat()
    git_commit = get_git_commit()

    metrics = {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "answerable_pass_rate": answerable_pass_rate,
        "multi_source_pass_rate": multi_source_pass_rate,
        "avg_citations_per_answerable": avg_citations_answerable,
        "invalid_citation_count": invalid_citation_count,
    }

    quality_gates = evaluate_quality_gates(
        metrics, thresholds, EVAL_MULTISOURCE_GATE_DEFINITIONS
    )
    gate_failures = [name for name, gate in quality_gates.items() if not gate.get("passed")]

    metadata = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "base_url": base_url,
        "source_ids": source_ids,
        "fixtures": [path.name for path in fixture_paths],
        "dataset": str(dataset_path),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "eval_multisource_results.json"
    report_path = OUT_DIR / "eval_multisource_report.md"

    output_payload = {
        "metadata": metadata,
        "metrics": metrics,
        "quality_gates": quality_gates,
        "cases": results,
    }

    json_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    write_report(report_path, results, metrics, metadata, quality_gates)

    print(f"Eval multisource complete: {passed_cases}/{total_cases} passed")
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")
    if gate_failures:
        print("Quality gate failures: " + ", ".join(gate_failures))

    if failed_cases or gate_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
