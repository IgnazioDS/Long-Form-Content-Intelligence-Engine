from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from _common.api_client import (  # noqa: E402
    find_source_by_filename,
    fixture_pdf_path,
    get_base_url,
    get_debug_chunk_ids,
    list_sources,
    upload_source,
    wait_for_source,
)

DATASET_PATH = Path(__file__).resolve().parent / "golden_sample.json"
OUT_DIR = Path(__file__).resolve().parent / "out"

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


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Eval dataset must be a JSON list")
    cases: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each eval case must be an object")
        for key in ("id", "question", "expected_behavior"):
            if key not in item:
                raise ValueError(f"Missing required field: {key}")
        cases.append(item)
    return cases


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


def normalize_keywords(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    keywords: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            keywords.append(item.strip())
    return keywords


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
) -> tuple[dict[str, Any], int]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()
    min_citations = case.get("min_citations")
    if min_citations is None:
        min_citations = 1 if expected == "ANSWERABLE" else 0
    try:
        min_citations = int(min_citations)
    except (TypeError, ValueError):
        min_citations = 1 if expected == "ANSWERABLE" else 0

    query_payload = {"question": question, "source_ids": [source_id]}
    response = client.post(f"{base_url}/query", json=query_payload)
    response.raise_for_status()
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

    answer_lower = answer.lower()
    must_include = normalize_keywords(case.get("must_include_keywords"))
    if must_include:
        missing = [kw for kw in must_include if kw.lower() not in answer_lower]
        if missing:
            failures.append("missing_keywords(" + ", ".join(missing) + ")")

    must_not_include = normalize_keywords(case.get("must_not_include_keywords"))
    if must_not_include:
        present = [kw for kw in must_not_include if kw.lower() in answer_lower]
        if present:
            failures.append("forbidden_keywords(" + ", ".join(present) + ")")

    invalid_citations = 0
    if citations:
        for citation in citations:
            citation_valid = True
            if not isinstance(citation, dict):
                citation_valid = False
                failures.append("invalid_citation_shape")
            else:
                chunk_id = citation.get("chunk_id")
                citation_source = citation.get("source_id")
                if not chunk_id or str(chunk_id) not in valid_chunk_ids:
                    citation_valid = False
                    failures.append(f"invalid_chunk_id({chunk_id})")
                if not citation_source or str(citation_source) != str(source_id):
                    citation_valid = False
                    failures.append(f"invalid_source_id({citation_source})")
            if not citation_valid:
                invalid_citations += 1

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "answer": answer,
        "citations": citations,
        "citations_count": citations_count,
        "min_citations": min_citations,
        "passed": not failures,
        "failures": failures,
    }
    return result, invalid_citations


def resolve_source_id(
    client: httpx.Client, base_url: str, pdf_path: Path
) -> tuple[str, dict[str, Any]]:
    sources = list_sources(client, base_url)
    existing = find_source_by_filename(sources, pdf_path.name)
    if existing and existing.get("status") == "READY":
        return str(existing["id"]), existing

    if existing and existing.get("status") in {"UPLOADED", "PROCESSING"}:
        ready = wait_for_source(client, base_url, str(existing["id"]))
        return str(ready["id"]), ready

    payload = upload_source(client, base_url, pdf_path, title="Eval Fixture: sample.pdf")
    ready = wait_for_source(client, base_url, str(payload["id"]))
    return str(ready["id"]), ready


def write_report(
    output_path: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    failed = [case for case in results if not case.get("passed")]

    lines: list[str] = ["# Evaluation Report", ""]
    lines.append(f"- Timestamp: {metadata.get('timestamp')}")
    lines.append(f"- Base URL: {metadata.get('base_url')}")
    lines.append(f"- Source ID: {metadata.get('source_id')}")
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
        "insufficient_evidence_pass_rate",
        "avg_citations_per_answerable",
        "invalid_citation_count",
    ):
        lines.append(f"| {key} | {metrics[key]} |")
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
    parser = argparse.ArgumentParser(description="Run local evaluation harness.")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--dataset", default=str(DATASET_PATH), help="Path to dataset JSON")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    pdf_path = fixture_pdf_path()
    base_url = get_base_url(args.base_url)

    cases = load_cases(dataset_path)

    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()

        source_id, source_payload = resolve_source_id(client, base_url, pdf_path)
        if source_payload.get("status") != "READY":
            raise RuntimeError("Source did not reach READY status")

        valid_chunk_ids = set(get_debug_chunk_ids(client, base_url, source_id))
        if not valid_chunk_ids:
            raise RuntimeError("No chunks available for citation validation")

        results: list[dict[str, Any]] = []
        invalid_citation_count = 0
        answerable_cases = 0
        answerable_passed = 0
        insufficient_cases = 0
        insufficient_passed = 0
        citation_total_for_answerable = 0

        for case in cases:
            result, invalid_count = evaluate_case(
                case, client, base_url, source_id, valid_chunk_ids
            )
            results.append(result)
            invalid_citation_count += invalid_count

            expected = result.get("expected_behavior")
            if expected == "ANSWERABLE":
                answerable_cases += 1
                citation_total_for_answerable += int(result.get("citations_count", 0))
                if result.get("passed"):
                    answerable_passed += 1
            elif expected == "INSUFFICIENT_EVIDENCE":
                insufficient_cases += 1
                if result.get("passed"):
                    insufficient_passed += 1

    total_cases = len(results)
    passed_cases = sum(1 for case in results if case.get("passed"))
    failed_cases = total_cases - passed_cases

    answerable_pass_rate = (
        round(answerable_passed / answerable_cases, 4) if answerable_cases else 1.0
    )
    insufficient_pass_rate = (
        round(insufficient_passed / insufficient_cases, 4) if insufficient_cases else 1.0
    )
    avg_citations_answerable = (
        round(citation_total_for_answerable / answerable_cases, 4)
        if answerable_cases
        else 0.0
    )

    timestamp = datetime.now(timezone.utc).isoformat()
    git_commit = get_git_commit()

    metrics = {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "answerable_pass_rate": answerable_pass_rate,
        "insufficient_evidence_pass_rate": insufficient_pass_rate,
        "avg_citations_per_answerable": avg_citations_answerable,
        "invalid_citation_count": invalid_citation_count,
    }

    metadata = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "base_url": base_url,
        "source_id": source_id,
        "dataset": str(dataset_path),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "eval_results.json"
    report_path = OUT_DIR / "eval_report.md"

    output_payload = {
        "metadata": metadata,
        "metrics": metrics,
        "cases": results,
    }

    json_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    write_report(report_path, results, metrics, metadata)

    print(f"Eval complete: {passed_cases}/{total_cases} passed")
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")

    if failed_cases:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
