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

VERDICT_KEYS = (
    "SUPPORTED",
    "WEAK_SUPPORT",
    "UNSUPPORTED",
    "CONTRADICTED",
    "CONFLICTING",
)

ALLOWED_RELATIONS = {"SUPPORTS", "CONTRADICTS", "RELATED"}
QUALITY_GATES = (
    ("invalid_evidence_id_count", "==", 0),
    ("answerable_pass_rate", ">=", 0.95),
    ("avg_claims_per_answerable", ">=", 2.0),
    ("unsupported_rate", "<=", 0.75),
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


def parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_score_valid(score: Any) -> bool:
    if isinstance(score, bool):
        return False
    if not isinstance(score, (int, float)):
        return False
    return 0.0 <= float(score) <= 1.0


def evaluate_case(
    case: dict[str, Any],
    client: httpx.Client,
    base_url: str,
    source_id: str,
    valid_chunk_ids: set[str],
) -> tuple[dict[str, Any], int, int, int]:
    question = str(case.get("question", "")).strip()
    expected = str(case.get("expected_behavior", "")).strip().upper()
    min_citations = parse_int(case.get("min_citations"), 1 if expected == "ANSWERABLE" else 0)
    min_claims = parse_int(case.get("min_claims"), 1 if expected == "ANSWERABLE" else 0)

    query_payload = {"question": question, "source_ids": [source_id]}
    response = client.post(f"{base_url}/query/verified", json=query_payload)
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())

    answer = str(payload.get("answer", "")).strip()
    citations = payload.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    citations_count = len(citations)

    raw_claims = payload.get("claims", [])
    claims: list[dict[str, Any]] = []
    failures: list[str] = []
    if isinstance(raw_claims, list):
        claims = [claim for claim in raw_claims if isinstance(claim, dict)]
        if len(claims) != len(raw_claims):
            failures.append("invalid_claim_shape")
    else:
        failures.append("claims_not_list")

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

    verdict_counts: dict[str, int] = {key: 0 for key in VERDICT_KEYS}
    unknown_verdicts = 0
    invalid_evidence_ids = 0
    unsupported_claims = 0
    evidence_count = 0

    for idx, claim in enumerate(claims):
        verdict = str(claim.get("verdict", "")).strip().upper()
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
        elif verdict:
            unknown_verdicts += 1

        if verdict == "UNSUPPORTED":
            unsupported_claims += 1

        support_score = claim.get("support_score")
        contradiction_score = claim.get("contradiction_score")
        if not is_score_valid(support_score):
            failures.append(
                f"support_score_out_of_range(index={idx}, value={support_score})"
            )
        if not is_score_valid(contradiction_score):
            failures.append(
                f"contradiction_score_out_of_range(index={idx}, value={contradiction_score})"
            )

        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            failures.append(f"invalid_evidence_list(index={idx})")
            continue

        evidence_count += len(evidence)
        for ev_idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                invalid_evidence_ids += 1
                failures.append(f"invalid_evidence_shape(index={idx}, evidence={ev_idx})")
                continue
            chunk_id = ev.get("chunk_id")
            relation = str(ev.get("relation", "")).strip().upper()
            evidence_valid = True
            if not chunk_id or str(chunk_id) not in valid_chunk_ids:
                evidence_valid = False
                failures.append(
                    f"invalid_evidence_chunk_id(index={idx}, evidence={ev_idx}, id={chunk_id})"
                )
            if relation not in ALLOWED_RELATIONS:
                evidence_valid = False
                failures.append(
                    f"invalid_evidence_relation(index={idx}, evidence={ev_idx}, relation={relation})"
                )
            if not evidence_valid:
                invalid_evidence_ids += 1

    claims_count = len(claims)

    if expected == "ANSWERABLE":
        if claims_count < min_claims:
            failures.append(f"insufficient_claims(expected>={min_claims}, got={claims_count})")
    elif expected == "INSUFFICIENT_EVIDENCE":
        if claims_count:
            invalid_insufficient = False
            for claim in claims:
                verdict = str(claim.get("verdict", "")).strip().upper()
                evidence = claim.get("evidence")
                if verdict != "UNSUPPORTED" or not isinstance(evidence, list) or evidence:
                    invalid_insufficient = True
                    break
            if invalid_insufficient:
                failures.append("insufficient_claims_not_unsupported")

    require_verdicts = {item.upper() for item in normalize_keywords(case.get("require_verdicts"))}
    if require_verdicts:
        if not any(v in require_verdicts for v in verdict_counts.keys() if verdict_counts[v]):
            failures.append("missing_required_verdict")

    forbid_verdicts = {item.upper() for item in normalize_keywords(case.get("forbid_verdicts"))}
    if forbid_verdicts:
        forbidden_present = any(
            verdict_counts.get(verdict, 0) > 0 for verdict in forbid_verdicts
        )
        if forbidden_present:
            failures.append("forbidden_verdict_present")

    unsupported_rate = (
        round(unsupported_claims / claims_count, 4) if claims_count else 0.0
    )
    max_unsupported_rate = parse_float(case.get("max_unsupported_rate"))
    if max_unsupported_rate is not None and claims_count:
        if unsupported_rate > max_unsupported_rate:
            failures.append(
                f"unsupported_rate_exceeded(threshold={max_unsupported_rate}, got={unsupported_rate})"
            )

    if unknown_verdicts:
        verdict_counts["UNKNOWN"] = unknown_verdicts

    result = {
        "id": case.get("id"),
        "question": question,
        "expected_behavior": expected,
        "answer": answer,
        "citations_count": citations_count,
        "claims_count": claims_count,
        "verdict_counts": verdict_counts,
        "unsupported_rate": unsupported_rate,
        "invalid_citation_count": invalid_citations,
        "invalid_evidence_id_count": invalid_evidence_ids,
        "passed": not failures,
        "failures": failures,
    }
    return result, invalid_citations, invalid_evidence_ids, evidence_count


def evaluate_quality_gates(
    metrics: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    results: list[dict[str, Any]] = []
    for key, operator, threshold in QUALITY_GATES:
        value = metrics.get(key)
        status = "PASS"
        if value is None:
            status = "FAIL"
            failures.append(f"{key}_missing")
        else:
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                status = "FAIL"
                failures.append(f"{key}_non_numeric(value={value})")
            else:
                threshold_value = float(threshold)
                if operator == "==":
                    if numeric_value != threshold_value:
                        status = "FAIL"
                        failures.append(
                            f"{key}_threshold(value={value}, expected=={threshold})"
                        )
                elif operator == ">=":
                    if numeric_value < threshold_value:
                        status = "FAIL"
                        failures.append(
                            f"{key}_threshold(value={value}, expected>={threshold})"
                        )
                elif operator == "<=":
                    if numeric_value > threshold_value:
                        status = "FAIL"
                        failures.append(
                            f"{key}_threshold(value={value}, expected<={threshold})"
                        )
                else:
                    status = "FAIL"
                    failures.append(f"{key}_unknown_operator({operator})")

        results.append(
            {
                "metric": key,
                "operator": operator,
                "threshold": threshold,
                "value": value,
                "status": status,
            }
        )
    return failures, results


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
    gate_results: list[dict[str, Any]],
    gate_failures: list[str],
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
        "invalid_evidence_id_count",
        "avg_claims_per_answerable",
        "avg_evidence_per_claim",
        "supported_rate",
        "weak_support_rate",
        "unsupported_rate",
        "contradicted_rate",
        "conflicting_rate",
    ):
        lines.append(f"| {key} | {metrics[key]} |")
    lines.append("")
    lines.append("## Quality Gates")
    if not gate_results:
        lines.append("- No gates configured.")
    else:
        lines.append("| Gate | Status | Value | Threshold |")
        lines.append("| --- | --- | --- | --- |")
        for gate in gate_results:
            lines.append(
                "| {metric} | {status} | {value} | {operator} {threshold} |".format(
                    metric=gate.get("metric"),
                    status=gate.get("status"),
                    value=gate.get("value"),
                    operator=gate.get("operator"),
                    threshold=gate.get("threshold"),
                )
            )
        if gate_failures:
            lines.append("")
            lines.append("- Gate failures: " + ", ".join(gate_failures))
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
        claim_total_for_answerable = 0
        total_claims = 0
        total_evidence = 0
        verdict_totals: dict[str, int] = {key: 0 for key in VERDICT_KEYS}

        for case in cases:
            result, invalid_citations, invalid_evidence_ids, evidence_count = evaluate_case(
                case, client, base_url, source_id, valid_chunk_ids
            )
            results.append(result)
            invalid_citation_count += invalid_citations
            invalid_evidence_id_count += invalid_evidence_ids
            total_claims += int(result.get("claims_count", 0))
            total_evidence += evidence_count

            verdict_counts = result.get("verdict_counts", {})
            if isinstance(verdict_counts, dict):
                for key in VERDICT_KEYS:
                    verdict_totals[key] += int(verdict_counts.get(key, 0))

            expected = result.get("expected_behavior")
            if expected == "ANSWERABLE":
                answerable_cases += 1
                citation_total_for_answerable += int(result.get("citations_count", 0))
                claim_total_for_answerable += int(result.get("claims_count", 0))
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
        round(claim_total_for_answerable / answerable_cases, 4)
        if answerable_cases
        else 0.0
    )
    avg_evidence_per_claim = (
        round(total_evidence / total_claims, 4) if total_claims else 0.0
    )

    supported_rate = round(
        verdict_totals["SUPPORTED"] / total_claims, 4
    ) if total_claims else 0.0
    weak_support_rate = round(
        verdict_totals["WEAK_SUPPORT"] / total_claims, 4
    ) if total_claims else 0.0
    unsupported_rate = round(
        verdict_totals["UNSUPPORTED"] / total_claims, 4
    ) if total_claims else 0.0
    contradicted_rate = round(
        verdict_totals["CONTRADICTED"] / total_claims, 4
    ) if total_claims else 0.0
    conflicting_rate = round(
        verdict_totals["CONFLICTING"] / total_claims, 4
    ) if total_claims else 0.0

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

    gate_failures, gate_results = evaluate_quality_gates(metrics)

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
        "quality_gates": {
            "failures": gate_failures,
            "results": gate_results,
        },
        "cases": results,
    }

    json_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    write_report(report_path, results, metrics, metadata, gate_results, gate_failures)

    print(f"Eval verified complete: {passed_cases}/{total_cases} passed")
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")
    if gate_failures:
        print("Quality gate failures: " + ", ".join(gate_failures))

    if failed_cases or gate_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
