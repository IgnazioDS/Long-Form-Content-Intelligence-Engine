from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
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

DATASET_PATH = Path(__file__).resolve().parent / "golden_verified.json"
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

ALLOWED_RELATIONS = {"SUPPORTS", "CONTRADICTS", "RELATED"}


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


def normalize_verdicts(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    verdicts: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            verdicts.append(item.strip().upper())
    return verdicts


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


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
) -> tuple[dict[str, Any], int, int]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()
    min_citations = case.get("min_citations")
    if min_citations is None:
        min_citations = 1 if expected == "ANSWERABLE" else 0
    try:
        min_citations = int(min_citations)
    except (TypeError, ValueError):
        min_citations = 1 if expected == "ANSWERABLE" else 0

    min_claims = case.get("min_claims")
    if min_claims is None:
        min_claims = 1 if expected == "ANSWERABLE" else 0
    try:
        min_claims = int(min_claims)
    except (TypeError, ValueError):
        min_claims = 1 if expected == "ANSWERABLE" else 0

    query_payload = {"question": question, "source_ids": [source_id]}
    response = client.post(f"{base_url}/query/verified", json=query_payload)
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())

    answer = str(payload.get("answer", "")).strip()
    citations = payload.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    citations_count = len(citations)

    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        claims = []
        claims_valid = False
    else:
        claims_valid = True

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

    if not claims_valid:
        failures.append("claims_not_list")

    verdict_counts: dict[str, int] = {}
    unsupported_claims = 0
    invalid_evidence_id_count = 0
    evidence_total = 0

    for claim in claims:
        if not isinstance(claim, dict):
            failures.append("invalid_claim_shape")
            continue
        verdict = str(claim.get("verdict", "")).strip().upper()
        if verdict:
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if verdict == "UNSUPPORTED":
            unsupported_claims += 1

        support_score = claim.get("support_score")
        contradiction_score = claim.get("contradiction_score")
        for score_name, score_value in (
            ("support_score", support_score),
            ("contradiction_score", contradiction_score),
        ):
            if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
                failures.append(f"invalid_{score_name}_type")
                continue
            if score_value < 0 or score_value > 1:
                failures.append(f"invalid_{score_name}_range({score_value})")

        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            failures.append("invalid_evidence_shape")
            continue
        evidence_total += len(evidence)
        for item in evidence:
            if not isinstance(item, dict):
                failures.append("invalid_evidence_item")
                continue
            chunk_id = item.get("chunk_id")
            relation = str(item.get("relation", "")).strip().upper()
            if not chunk_id or str(chunk_id) not in valid_chunk_ids:
                invalid_evidence_id_count += 1
                failures.append(f"invalid_evidence_chunk_id({chunk_id})")
            if relation not in ALLOWED_RELATIONS:
                failures.append(f"invalid_evidence_relation({relation})")

    claims_count = len(claims)

    if expected == "ANSWERABLE":
        if claims_count < min_claims:
            failures.append(f"insufficient_claims(expected>={min_claims}, got={claims_count})")
    elif expected == "INSUFFICIENT_EVIDENCE":
        if claims_count:
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                verdict = str(claim.get("verdict", "")).strip().upper()
                evidence = claim.get("evidence", [])
                evidence_is_empty = isinstance(evidence, list) and not evidence
                if verdict != "UNSUPPORTED" or not evidence_is_empty:
                    failures.append("insufficient_evidence_claims_present")
                    break

    require_verdicts = normalize_verdicts(case.get("require_verdicts"))
    if require_verdicts:
        if not any(verdict in require_verdicts for verdict in verdict_counts):
            failures.append("missing_required_verdicts(" + ", ".join(require_verdicts) + ")")

    forbid_verdicts = normalize_verdicts(case.get("forbid_verdicts"))
    if forbid_verdicts:
        if any(verdict in forbid_verdicts for verdict in verdict_counts):
            failures.append("forbidden_verdicts_present(" + ", ".join(forbid_verdicts) + ")")

    unsupported_rate = (
        round(unsupported_claims / claims_count, 4) if claims_count else 0.0
    )
    max_unsupported_rate = case.get("max_unsupported_rate")
    if max_unsupported_rate is not None:
        try:
            max_unsupported_rate = float(max_unsupported_rate)
        except (TypeError, ValueError):
            max_unsupported_rate = None
        if max_unsupported_rate is not None and unsupported_rate > max_unsupported_rate:
            failures.append(
                "unsupported_rate_exceeded"
                f"(max={max_unsupported_rate}, got={unsupported_rate})"
            )

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "answer": answer,
        "citations_count": citations_count,
        "claims_count": claims_count,
        "evidence_count": evidence_total,
        "verdict_counts": verdict_counts,
        "unsupported_rate": unsupported_rate,
        "invalid_citation_count": invalid_citations,
        "invalid_evidence_id_count": invalid_evidence_id_count,
        "passed": not failures,
        "failures": failures,
    }
    return result, invalid_citations, invalid_evidence_id_count


def write_report(
    output_path: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    failed = [case for case in results if not case.get("passed")]

    lines: list[str] = ["# Evaluation Report (Verified)", ""]
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
        "invalid_evidence_id_count",
        "avg_claims_per_answerable",
        "avg_evidence_per_claim",
    ):
        lines.append(f"| {key} | {metrics[key]} |")
    lines.append("")
    lines.append("## Verdict Distribution")
    lines.append("| Verdict | Rate |")
    lines.append("| --- | --- |")
    for key in (
        "supported_rate",
        "weak_support_rate",
        "unsupported_rate",
        "contradicted_rate",
        "conflicting_rate",
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
    parser = argparse.ArgumentParser(description="Run verified evaluation harness.")
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
        invalid_evidence_id_count = 0
        answerable_cases = 0
        answerable_passed = 0
        insufficient_cases = 0
        insufficient_passed = 0
        citation_total_for_answerable = 0
        claims_total_for_answerable = 0
        total_claims = 0
        total_evidence = 0
        verdict_counts_total = {
            "SUPPORTED": 0,
            "WEAK_SUPPORT": 0,
            "UNSUPPORTED": 0,
            "CONTRADICTED": 0,
            "CONFLICTING": 0,
        }

        for case in cases:
            result, invalid_count, invalid_evidence = evaluate_case(
                case, client, base_url, source_id, valid_chunk_ids
            )
            results.append(result)
            invalid_citation_count += invalid_count
            invalid_evidence_id_count += invalid_evidence

            expected = result.get("expected_behavior")
            claims_count = int(result.get("claims_count", 0))
            total_claims += claims_count
            total_evidence += int(result.get("evidence_count", 0))

            verdict_counts = result.get("verdict_counts", {})
            if isinstance(verdict_counts, dict):
                for verdict, count in verdict_counts.items():
                    if verdict in verdict_counts_total and isinstance(count, int):
                        verdict_counts_total[verdict] += count

            if expected == "ANSWERABLE":
                answerable_cases += 1
                citation_total_for_answerable += int(result.get("citations_count", 0))
                claims_total_for_answerable += claims_count
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
    avg_claims_answerable = (
        round(claims_total_for_answerable / answerable_cases, 4)
        if answerable_cases
        else 0.0
    )
    avg_evidence_per_claim = (
        round(total_evidence / total_claims, 4) if total_claims else 0.0
    )

    supported_rate = (
        round(verdict_counts_total["SUPPORTED"] / total_claims, 4)
        if total_claims
        else 0.0
    )
    weak_support_rate = (
        round(verdict_counts_total["WEAK_SUPPORT"] / total_claims, 4)
        if total_claims
        else 0.0
    )
    unsupported_rate = (
        round(verdict_counts_total["UNSUPPORTED"] / total_claims, 4)
        if total_claims
        else 0.0
    )
    contradicted_rate = (
        round(verdict_counts_total["CONTRADICTED"] / total_claims, 4)
        if total_claims
        else 0.0
    )
    conflicting_rate = (
        round(verdict_counts_total["CONFLICTING"] / total_claims, 4)
        if total_claims
        else 0.0
    )

    timestamp = datetime.now(UTC).isoformat()
    git_commit = get_git_commit()

    metrics = {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "answerable_pass_rate": answerable_pass_rate,
        "insufficient_evidence_pass_rate": insufficient_pass_rate,
        "avg_citations_per_answerable": avg_citations_answerable,
        "invalid_citation_count": invalid_citation_count,
        "invalid_evidence_id_count": invalid_evidence_id_count,
        "avg_claims_per_answerable": avg_claims_answerable,
        "avg_evidence_per_claim": avg_evidence_per_claim,
        "supported_rate": supported_rate,
        "weak_support_rate": weak_support_rate,
        "unsupported_rate": unsupported_rate,
        "contradicted_rate": contradicted_rate,
        "conflicting_rate": conflicting_rate,
    }

    metadata = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "base_url": base_url,
        "source_id": source_id,
        "dataset": str(dataset_path),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "eval_verified_results.json"
    report_path = OUT_DIR / "eval_verified_report.md"

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
