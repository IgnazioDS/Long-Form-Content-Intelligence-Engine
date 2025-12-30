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
    delete_source,
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
THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"
DEFAULT_READY_TIMEOUT_SECONDS = 60
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
POST_RETRY_LIMIT = 3
RETRY_BACKOFF_SECONDS = 0.5
HEALTH_POLL_INTERVAL_SECONDS = 2.0

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
CONTRADICTION_PREFIX_MARKER = "contradictions detected in the source material"

VERDICT_KEYS = (
    "SUPPORTED",
    "WEAK_SUPPORT",
    "UNSUPPORTED",
    "CONTRADICTED",
    "CONFLICTING",
)

ALLOWED_RELATIONS = {"SUPPORTS", "CONTRADICTS", "RELATED"}
EVAL_VERIFIED_GATE_DEFINITIONS = (
    ("invalid_citation_count_max", "invalid_citation_count", "<="),
    ("invalid_evidence_id_count_max", "invalid_evidence_id_count", "<="),
    ("answerable_pass_rate_min", "answerable_pass_rate", ">="),
    ("avg_claims_per_answerable_min", "avg_claims_per_answerable", ">="),
    ("unsupported_rate_max", "unsupported_rate", "<="),
)

EVAL_VERIFIED_CONFLICTS_GATE_DEFINITIONS = (
    ("invalid_evidence_id_count_max", "invalid_evidence_id_count", "<="),
    (
        "contradicted_or_conflicting_rate_min",
        "contradicted_or_conflicting_rate",
        ">=",
    ),
    (
        "contradiction_detection_rate_min",
        "contradiction_detection_rate",
        ">=",
    ),
)


def get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_dataset(path: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases_payload = payload
        fixture_name: str | None = None
        profile_name: str | None = None
    elif isinstance(payload, dict):
        cases_payload = payload.get("cases", [])
        fixture_raw = payload.get("fixture")
        profile_raw = payload.get("profile")
        fixture_name = str(fixture_raw).strip() if fixture_raw else None
        profile_name = str(profile_raw).strip() if profile_raw else None
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
    return cases, fixture_name, profile_name


def load_thresholds(path: Path, section: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Thresholds file must be a JSON object")
    thresholds = payload.get(section)
    if not isinstance(thresholds, dict):
        raise ValueError(f"Missing thresholds section: {section}")
    return thresholds


def resolve_fixture_path(fixture_name: str | None) -> Path:
    if fixture_name:
        candidate = Path(fixture_name)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parents[1] / "fixtures" / fixture_name
        if not candidate.exists():
            raise FileNotFoundError(f"Fixture not found: {candidate}")
        return candidate
    return fixture_pdf_path()


def is_conflicts_dataset(
    dataset_path: Path, fixture_name: str | None, profile_name: str | None
) -> bool:
    profile = (profile_name or "").strip().lower()
    if profile in {"conflicts", "eval_verified_conflicts"}:
        return True
    if fixture_name and fixture_name.strip().lower() == "conflicts.pdf":
        return True
    return "conflicts" in dataset_path.stem.lower()


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


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def is_score_valid(score: Any) -> bool:
    if isinstance(score, bool):
        return False
    if not isinstance(score, (int, float)):
        return False
    return 0.0 <= float(score) <= 1.0


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
    response = post_with_retries(client, f"{base_url}/query/verified", query_payload)
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

    summary_payload = payload.get("verification_summary")
    if summary_payload is not None and not isinstance(summary_payload, dict):
        failures.append("invalid_verification_summary_shape")
        summary_payload = None
    if summary_payload is None:
        failures.append("missing_verification_summary")

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
                    "invalid_evidence_relation("
                    f"index={idx}, evidence={ev_idx}, relation={relation})"
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
                "unsupported_rate_exceeded("
                f"threshold={max_unsupported_rate}, got={unsupported_rate})"
            )

    summary_has_contradictions: bool | None = None
    if summary_payload:
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

        summary_has_contradictions = summary_payload.get("has_contradictions")
        if not isinstance(summary_has_contradictions, bool):
            failures.append("invalid_summary_has_contradictions")
            summary_has_contradictions = None

        summary_overall = str(summary_payload.get("overall_verdict", "")).strip().upper()
        if summary_overall not in {"OK", "HAS_CONTRADICTIONS", "INSUFFICIENT_EVIDENCE"}:
            failures.append("invalid_summary_overall_verdict")

        if len(summary_counts) == len(summary_counts_raw):
            for verdict_key, count in summary_counts.items():
                expected_count = verdict_counts.get(verdict_key, 0)
                if count != expected_count:
                    failures.append(
                        "summary_count_mismatch("
                        f"verdict={verdict_key}, expected={expected_count}, got={count})"
                    )

        expected_has_contradictions = (
            verdict_counts.get("CONTRADICTED", 0)
            + verdict_counts.get("CONFLICTING", 0)
        ) > 0
        if summary_has_contradictions is not None:
            if summary_has_contradictions != expected_has_contradictions:
                failures.append("summary_has_contradictions_mismatch")

    expect_contradictions = parse_bool(case.get("expect_contradictions"))
    require_conflict_prefix = parse_bool(case.get("require_conflict_prefix"))
    prefix_present = CONTRADICTION_PREFIX_MARKER in answer.lower()
    if expect_contradictions and not summary_has_contradictions:
        failures.append("expected_contradictions_missing")
    if require_conflict_prefix and not prefix_present:
        failures.append("missing_conflict_prefix")

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
        "has_contradictions": summary_has_contradictions,
        "expect_contradictions": expect_contradictions,
        "conflict_prefix_present": prefix_present,
        "passed": not failures,
        "failures": failures,
    }
    return result, invalid_citations, invalid_evidence_ids, evidence_count


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

    payload = upload_source(client, base_url, pdf_path, title="Eval Fixture: sample.pdf")
    try:
        ready = wait_for_source(client, base_url, str(payload["id"]), timeout_s=timeout_s)
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc} (check worker logs and OPENAI_API_KEY/AI_PROVIDER settings)"
        ) from exc
    return str(ready["id"]), ready


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
        "contradicted_or_conflicting_rate",
        "contradiction_detection_rate",
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
    parser = argparse.ArgumentParser(description="Run verified evaluation harness.")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--dataset", default=str(DATASET_PATH), help="Path to dataset JSON")
    parser.add_argument(
        "--fixture",
        default=None,
        help="Fixture PDF filename or path (overrides dataset fixture)",
    )
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

    cases, dataset_fixture, dataset_profile = load_dataset(dataset_path)
    fixture_name = args.fixture or dataset_fixture
    if not fixture_name and (dataset_profile or "").strip().lower() in {
        "conflicts",
        "eval_verified_conflicts",
    }:
        fixture_name = "conflicts.pdf"
    pdf_path = resolve_fixture_path(fixture_name)

    is_conflicts_fixture = is_conflicts_dataset(
        dataset_path, fixture_name, dataset_profile
    )
    thresholds_section = (
        "eval_verified_conflicts" if is_conflicts_fixture else "eval_verified"
    )
    gate_definitions = (
        EVAL_VERIFIED_CONFLICTS_GATE_DEFINITIONS
        if is_conflicts_fixture
        else EVAL_VERIFIED_GATE_DEFINITIONS
    )
    thresholds = load_thresholds(thresholds_path, thresholds_section)

    with httpx.Client(timeout=float(http_timeout)) as client:
        wait_for_health(client, base_url, ready_timeout)

        source_id, source_payload = resolve_source_id(
            client, base_url, pdf_path, timeout_s=ready_timeout
        )
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
        expected_contradiction_cases = 0
        detected_contradiction_cases = 0

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

            if result.get("expect_contradictions"):
                expected_contradiction_cases += 1
                if result.get("has_contradictions"):
                    detected_contradiction_cases += 1

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
    contradicted_or_conflicting_rate = round(
        (verdict_totals["CONTRADICTED"] + verdict_totals["CONFLICTING"]) / total_claims,
        4,
    ) if total_claims else 0.0
    contradiction_detection_rate = (
        round(detected_contradiction_cases / expected_contradiction_cases, 4)
        if expected_contradiction_cases
        else 1.0
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
        "contradicted_or_conflicting_rate": contradicted_or_conflicting_rate,
        "contradiction_detection_rate": contradiction_detection_rate,
    }

    quality_gates = evaluate_quality_gates(metrics, thresholds, gate_definitions)
    gate_failures = [name for name, gate in quality_gates.items() if not gate.get("passed")]

    metadata = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "base_url": base_url,
        "source_id": source_id,
        "dataset": str(dataset_path),
        "fixture": pdf_path.name,
        "profile": dataset_profile,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "eval_verified_results.json"
    report_path = OUT_DIR / "eval_verified_report.md"

    output_payload = {
        "metadata": metadata,
        "metrics": metrics,
        "quality_gates": quality_gates,
        "cases": results,
    }

    json_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    write_report(report_path, results, metrics, metadata, quality_gates)

    print(f"Eval verified complete: {passed_cases}/{total_cases} passed")
    print(f"Results: {json_path}")
    print(f"Report: {report_path}")
    if gate_failures:
        print("Quality gate failures: " + ", ".join(gate_failures))

    if failed_cases or gate_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
